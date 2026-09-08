"""Qualify security-sensitive native consumers inside the actual API image.

Run as the service user. Promotion mounts this reviewed source over the image
rather than trusting an image-provided checker. Failure prevents signing.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
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


def main() -> None:
    require(os.getuid() == 10001, "Qualification must run as service UID 10001")
    print(json.dumps({"sqlite": verify_sqlite()}, sort_keys=True))


if __name__ == "__main__":
    main()
