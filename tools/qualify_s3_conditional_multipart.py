"""Qualify conditional multipart CAS semantics on the configured S3 endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared import storage
from shared.artifact_manifest import content_addressed_blob_key
from shared.checksums import sha256_file
from shared.config import S3_BUCKET


MINIMUM_PROBE_BYTES: Final = 5 * 1024**2 + 1
MAXIMUM_PROBE_BYTES: Final = 64 * 1024**2
CONDITIONAL_CONFLICT_CODES: Final = {
    "409",
    "412",
    "ConditionalRequestConflict",
    "PreconditionFailed",
}


def _write_probe(path: Path, size_bytes: int) -> None:
    remaining = size_bytes
    with path.open("wb") as stream:
        while remaining:
            chunk = secrets.token_bytes(min(1024**2, remaining))
            stream.write(chunk)
            remaining -= len(chunk)


def _conditional_overwrite_probe(key: str) -> str:
    """Return the expected conflict code or fail if the provider overwrites."""

    client = storage._get_client()
    challenger = b"droneai-conditional-multipart-overwrite-probe"
    response = client.create_multipart_upload(
        Bucket=S3_BUCKET,
        Key=key,
        Metadata={"sha256": hashlib.sha256(challenger).hexdigest()},
    )
    upload_id = response.get("UploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise OSError("S3 did not return an upload ID for the overwrite probe")
    completed = False
    try:
        part = client.upload_part(
            Bucket=S3_BUCKET,
            Key=key,
            UploadId=upload_id,
            PartNumber=1,
            Body=challenger,
            ContentLength=len(challenger),
        )
        etag = part.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise OSError("S3 overwrite probe part has no ETag")
        try:
            client.complete_multipart_upload(
                Bucket=S3_BUCKET,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [{"ETag": etag, "PartNumber": 1}]},
                IfNoneMatch="*",
            )
            completed = True
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in CONDITIONAL_CONFLICT_CODES:
                raise
            return code
        raise RuntimeError(
            "S3 endpoint ignored If-None-Match on CompleteMultipartUpload"
        )
    finally:
        if not completed:
            storage._abort_multipart_quietly(
                client,
                bucket=S3_BUCKET,
                key=key,
                upload_id=upload_id,
            )


def qualify(probe_path: Path, *, organization_id: str) -> dict[str, Any]:
    """Publish, challenge, verify, reuse and remove one random CAS probe."""

    size = probe_path.stat().st_size
    if not MINIMUM_PROBE_BYTES <= size <= MAXIMUM_PROBE_BYTES:
        raise ValueError(
            f"Qualification probe must contain {MINIMUM_PROBE_BYTES} to "
            f"{MAXIMUM_PROBE_BYTES} bytes"
        )
    digest = sha256_file(probe_path)
    key = content_addressed_blob_key(digest, organization_id=organization_id)
    if storage.file_exists(key):
        raise RuntimeError("Random qualification object unexpectedly existed")
    created = False
    cleanup_verified = False
    try:
        first = storage.publish_content_addressed_file(
            probe_path,
            force_multipart=True,
            organization_id=organization_id,
        )
        if first.reused:
            raise RuntimeError("Qualification publication unexpectedly reported reuse")
        created = True
        conflict_code = _conditional_overwrite_probe(key)
        storage.verify_object_checksum(key, digest)
        if storage.get_object_size(key) != size:
            raise OSError("S3 qualification object size changed after conflict")
        second = storage.publish_content_addressed_file(
            probe_path,
            force_multipart=True,
            organization_id=organization_id,
        )
        if not second.reused or second.transferred_bytes != 0:
            raise RuntimeError("Second CAS publication did not reuse the verified object")
        return {
            "status": "passed",
            "bucket": S3_BUCKET,
            "key": key,
            "size_bytes": size,
            "conditional_conflict_code": conflict_code,
            "cleanup_verified": True,
        }
    finally:
        if created or storage.file_exists(key):
            storage.delete_object(key)
            cleanup_verified = not storage.file_exists(key)
            if not cleanup_verified:
                raise RuntimeError("S3 qualification object cleanup could not be verified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size-mib",
        type=int,
        default=6,
        help="Random multipart probe size in MiB (default: 6, maximum: 64)",
    )
    parser.add_argument("--organization-id", required=True, help="Organization owning the temporary CAS probe")
    arguments = parser.parse_args()
    size_bytes = arguments.size_mib * 1024**2
    if not MINIMUM_PROBE_BYTES <= size_bytes <= MAXIMUM_PROBE_BYTES:
        parser.error("--size-mib must be between 6 and 64")
    with tempfile.TemporaryDirectory(prefix="droneai-s3-qualification-") as directory:
        probe_path = Path(directory) / "multipart-probe.bin"
        _write_probe(probe_path, size_bytes)
        result = qualify(probe_path, organization_id=arguments.organization_id)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
