"""Download a pinned official Windows SDK to a workspace directory (no installer)."""
import argparse
import hashlib
import pathlib
import urllib.request
import zipfile

VERSION = "10.0.26100.9169"
PACKAGES = {
    "microsoft.windows.sdk.cpp": "475269434dcd808a67853773272f972c3229c0e10c3ddc821290e70cc0f6904d",
    "microsoft.windows.sdk.cpp.x64": "df6226a051e320942abfbd57848b43d18772996ecd66beadad240f2a56ed2f7b",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=pathlib.Path)
    destination = parser.parse_args().destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name, expected in PACKAGES.items():
        archive = destination / (name + ".nupkg")
        if not archive.exists():
            url = f"https://api.nuget.org/v3-flatcontainer/{name}/{VERSION}/{name}.{VERSION}.nupkg"
            urllib.request.urlretrieve(url, archive)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise ValueError(f"SDK archive checksum mismatch: {archive}")
        target = destination / name
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (target / member.filename).resolve().is_relative_to(target):
                    raise ValueError("SDK archive path escapes destination")
            package.extractall(target)
        print(name, VERSION, digest)


if __name__ == "__main__":
    main()
