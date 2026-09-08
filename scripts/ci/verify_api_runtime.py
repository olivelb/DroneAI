"""Qualify security-sensitive native consumers inside the actual API image.

Run as the service user. Promotion mounts this reviewed source over the image
rather than trusting an image-provided checker. Failure prevents signing.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_sqlite() -> dict:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    installed = subprocess.check_output(
        ["dpkg-query", "--show", "--showformat=${Version}", "libsqlite3-0"], text=True
    )
    require(installed == "3.53.4-2", f"Unqualified SQLite package: {installed}")
    require(sqlite3.sqlite_version_info >= (3, 53, 2), "Python loads vulnerable SQLite FTS5")
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE VIRTUAL TABLE pages USING fts5(body)")
        database.execute("INSERT INTO pages VALUES (?)", ("drone mapping",))
        result = database.execute("SELECT body FROM pages WHERE pages MATCH 'drone'").fetchall()
        require(result == [("drone mapping",)], "SQLite FTS5 roundtrip failed")
        database.execute("INSERT INTO pages(pages) VALUES ('integrity-check')")

    # Rasterio/GDAL wheels load a separate SQLite. Inspect every bundled copy,
    # never infer its version or compile options from Python's sqlite3 module.
    libraries = sorted((Path(rasterio.__file__).parent.parent / "rasterio.libs").glob("libsqlite3*.so*"))
    require(bool(libraries), "Rasterio SQLite layout changed; audit its native dependency")
    bundled = []
    for path in libraries:
        library = ctypes.CDLL(str(path))
        library.sqlite3_libversion.restype = ctypes.c_char_p
        version = library.sqlite3_libversion().decode()
        library.sqlite3_compileoption_used.argtypes = [ctypes.c_char_p]
        library.sqlite3_compileoption_used.restype = ctypes.c_int
        fts5 = bool(library.sqlite3_compileoption_used(b"ENABLE_FTS5"))
        require(not fts5 or tuple(map(int, version.split("."))) >= (3, 53, 2),
                f"Rasterio loads vulnerable SQLite FTS5: {path.name} {version}")
        bundled.append({"library": path.name, "version": version, "fts5": fts5})

    # Exercise a real GDAL SQLite consumer, not just dlopen/import success.
    with tempfile.TemporaryDirectory(prefix="api-sqlite-") as directory:
        path = Path(directory) / "roundtrip.gpkg"
        pixels = np.arange(256, dtype=np.uint8).reshape(16, 16)
        with rasterio.open(path, "w", driver="GPKG", width=16, height=16,
                           count=1, dtype="uint8", crs="EPSG:4326",
                           transform=from_origin(2, 46, 0.001, 0.001),
                           TILE_FORMAT="PNG") as dataset:
            dataset.write(pixels, 1)
        with rasterio.open(path) as dataset:
            require(np.array_equal(dataset.read(1), pixels), "GDAL GeoPackage pixel roundtrip failed")
            require(dataset.crs.to_epsg() == 4326, "GDAL GeoPackage CRS roundtrip failed")
    return {"package": installed, "python": sqlite3.sqlite_version,
            "rasterio": bundled, "fts5_roundtrip": True, "geopackage_roundtrip": True}



def verify_acl() -> dict:
    expected = {"libacl1": "2.4.0-1", "tar": "1.35+dfsg-5"}
    for package, version in expected.items():
        installed = subprocess.check_output(
            ["dpkg-query", "--show", "--showformat=${Version}", package], text=True
        )
        require(installed == version, f"Unqualified {package}: {installed}")
    library = ctypes.CDLL("libacl.so.1", use_errno=True)
    library.acl_from_text.argtypes = [ctypes.c_char_p]
    library.acl_from_text.restype = ctypes.c_void_p
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    library.acl_get_file_at.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    library.acl_get_file_at.restype = ctypes.c_void_p
    library.acl_set_file_at.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_void_p]
    library.acl_set_file_at.restype = ctypes.c_int
    # Linux constants from fcntl.h and the signed ACL 2.4.0 source headers.
    access, nofollow, empty_path = 0x8000, 0x100, 0x1000
    acl = library.acl_from_text(b"user::rw-,user:10002:r--,group::r--,mask::r--,other::---")
    require(bool(acl), "Cannot construct extended ACL")
    try:
        with tempfile.TemporaryDirectory(prefix="api-acl-") as directory:
            root = Path(directory)
            source = root / "source"
            source.write_text("original")
            with source.open("rb") as held:
                result = library.acl_set_file_at(held.fileno(), b"", empty_path, access, acl)
                require(result == 0, f"ACL fd write failed: errno {ctypes.get_errno()}")
            expected_xattr = os.getxattr(source, "system.posix_acl_access")
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                read_acl = library.acl_get_file_at(directory_fd, b"source", nofollow, access)
                require(bool(read_acl), "ACL directory-relative read failed")
                library.acl_free(read_acl)
                (root / "link").symlink_to("source")
                read_link = library.acl_get_file_at(directory_fd, b"link", nofollow, access)
                if read_link:
                    library.acl_free(read_link)
                require(not read_link, "ACL nofollow unexpectedly read the symlink target")
                result = library.acl_set_file_at(directory_fd, b"link", nofollow, access, acl)
                require(result == -1, "ACL nofollow unexpectedly wrote the symlink target")
                require(os.getxattr(source, "system.posix_acl_access") == expected_xattr,
                        "Symlink target ACL changed")
            finally:
                os.close(directory_fd)
            # Existing consumers must retain ACLs with the new library. tar is
            # upgraded together because Debian #1141146 fixes its symbol clash.
            subprocess.run(["cp", "--preserve=all", "source", "copy"], cwd=root, check=True)
            subprocess.run(["sed", "-i", "s/original/edited/", "copy"], cwd=root, check=True)
            require((root / "copy").read_text() == "edited", "sed roundtrip failed")
            require(os.getxattr(root / "copy", "system.posix_acl_access") == expected_xattr,
                    "cp/sed lost extended ACL")
            subprocess.run(["tar", "--acls", "-cf", "archive.tar", "source"], cwd=root, check=True)
            (root / "extracted").mkdir()
            subprocess.run(["tar", "--acls", "-xf", "archive.tar", "-C", "extracted"],
                           cwd=root, check=True)
            extracted = root / "extracted/source"
            require(extracted.read_text() == "original", "tar content roundtrip failed")
            require(os.getxattr(extracted, "system.posix_acl_access") == expected_xattr,
                    "tar lost extended ACL")
    finally:
        library.acl_free(acl)
    return {"packages": expected, "fd_and_nofollow": True, "cp_sed_tar_roundtrip": True}



def verify_absent_components() -> dict:
    packages = ("gzip", "ncurses-bin", "systemd-homed", "systemd", "mount", "util-linux", "libmount1")
    for package in packages:
        result = subprocess.run(
            ["dpkg-query", "--show", "--showformat=${db:Status-Status}", package],
            capture_output=True, text=True, check=False,
        )
        require(result.returncode in (0, 1), f"Cannot inspect package {package}: {result.stderr}")
        require(result.stdout.strip() != "installed", f"Vulnerable component returned: {package}")
    executables = ("gzip", "infocmp", "systemd-homed", "homectl", "mount", "umount", "nsenter")
    directories = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin",
                   "/usr/local/sbin", "/lib/systemd", "/usr/lib/systemd")
    for executable in executables:
        require(shutil.which(executable) is None, f"Vulnerable executable in PATH: {executable}")
        for directory in directories:
            path = Path(directory) / executable
            require(not path.exists() and not path.is_symlink(), f"Vulnerable executable returned: {path}")
    # Mount-hook defects live in libmount, not UUID. Check the library too,
    # including unpackaged copies in normal dynamic-loader directories.
    for directory in ("/lib", "/usr/lib", "/usr/local/lib"):
        require(not list(Path(directory).glob("**/libmount.so*")), "libmount returned to the runtime")
    return {"absent_packages": packages, "absent_executables": executables, "libmount_absent": True}


def main() -> None:
    require(os.getuid() == 10001, "Qualification must run as service UID 10001")
    print(json.dumps({"components": verify_absent_components(), "sqlite": verify_sqlite(), "acl": verify_acl()}, sort_keys=True))


if __name__ == "__main__":
    main()
