#!/usr/bin/env python3
"""Convert an immutable GSTile bundle to base/SH-only storage without rebuilding LOD."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.gstile_manifest import validate_gstile_manifest
from shared.gstile_streams import read_pack_content, split_q96, stream_metadata


def convert_bundle(source: Path, output: Path, *, workers: int = 4, progress=None, resume: bool = False) -> str:
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Output must be outside the source bundle")
    pending = output.with_name(output.name + ".partial")
    if output.exists() or (pending.exists() and not resume):
        raise ValueError("Output or partial output already exists; choose a new directory")
    if workers < 1 or workers > 16:
        raise ValueError("Workers must be between 1 and 16")
    manifest = json.loads((source / "manifest.json").read_bytes())
    validate_gstile_manifest(manifest)
    paths = [p["path"] + "." + kind for p in manifest["packs"] for kind in ("base", "sh")]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate output stream paths")
    pending.mkdir(parents=True, exist_ok=resume)
    if pending.is_symlink() or pending.resolve() != pending:
        raise ValueError("Partial output must not redirect through a link")

    def convert(pack):
        data = read_pack_content(source, pack)
        streams = split_q96(data)
        result = dict(pack)
        result.pop("encodings", None)
        result.update(storage="streams", q96Header=data[:32].hex(), streams={"version": 1})
        for kind, content in streams.items():
            relative = pack["path"] + "." + kind
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.resolve().is_relative_to(pending):
                raise ValueError("Output link escapes partial bundle")
            metadata = stream_metadata(content, relative)
            reusable = (
                resume
                and destination.is_file()
                and destination.stat().st_size == len(content)
                and hashlib.sha256(destination.read_bytes()).hexdigest() == metadata["sha256"]
            )
            if not reusable:
                temporary = destination.with_name(destination.name + ".writing")
                if temporary.is_symlink():
                    raise ValueError("Temporary output must not be a link")
                with temporary.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Verify persisted bytes before publishing an individual stream.
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != metadata["sha256"]:
                    raise ValueError("Written attribute stream SHA256 mismatch")
                temporary.replace(destination)
            result["streams"][kind] = metadata
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        converted = []
        for index, pack in enumerate(pool.map(convert, manifest["packs"]), 1):
            converted.append(pack)
            if progress:
                progress(index, len(manifest["packs"]))
    manifest["packs"] = converted
    stored = sum(p["streams"][kind]["byteLength"] for p in converted for kind in ("base", "sh"))
    manifest["statistics"]["attributeStreamBytes"] = stored
    manifest["statistics"]["storedPackBytes"] = stored
    manifest["bundleId"] = None
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest["bundleId"] = "sha256:" + digest
    validate_gstile_manifest(manifest)
    expected = {p["streams"][kind]["path"] for p in converted for kind in ("base", "sh")}
    actual = {p.relative_to(pending).as_posix() for p in pending.rglob("*") if p.is_file()}
    if actual - expected - {"manifest.json", "manifest.json.writing"}:
        raise ValueError("Unexpected files in partial output; refusing publication")
    manifest_tmp = pending / "manifest.json.writing"
    if manifest_tmp.is_symlink():
        raise ValueError("Temporary manifest must not be a link")
    with manifest_tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        handle.flush()
        os.fsync(handle.fileno())
    manifest_tmp.replace(pending / "manifest.json")
    pending.rename(output)
    return manifest["bundleId"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true", help="Reverify and continue an existing .partial directory")
    args = parser.parse_args()
    started = time.monotonic()

    def progress(done, total):
        if done == 1 or done % 100 == 0 or done == total:
            print(
                json.dumps({"packs": done, "total": total, "elapsedSeconds": round(time.monotonic() - started, 2)}),
                flush=True,
            )

    print(
        convert_bundle(args.source, args.output, workers=args.workers, progress=progress, resume=args.resume),
        flush=True,
    )
