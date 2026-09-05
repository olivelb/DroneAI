"""Explicit version-aware erasure, separate from logical object deletion."""

from typing import Any


def erase_prefix_versions(client: Any, s3_prefix: str, bucket: str, maximum_attempts: int) -> int:
    """Physically erase versions and delete markers in an explicitly scoped prefix.

    Caller must have drained all writers and checked dependencies and holds.
    Fails closed on Object Lock/permission errors; empty ListObjects is not proof.
    This is an opt-in operation, never the default retention policy.
    """
    if not s3_prefix.strip("/") or not s3_prefix.endswith("/"):
        raise ValueError("Version erasure requires a nonempty directory prefix")
    deleted = 0
    for _attempt in range(maximum_attempts):
        found = False
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix):
            versions = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for field in ("Versions", "DeleteMarkers")
                for item in page.get(field, [])
            ]
            for offset in range(0, len(versions), 1000):
                batch = versions[offset:offset + 1000]
                found = True
                response = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
                if response.get("Errors"):
                    raise RuntimeError("Version erasure failed; permissions or retention may prohibit deletion")
                deleted += len(batch)
        if not found:
            return deleted
    raise RuntimeError("Version erasure did not converge; remaining versions require reconciliation")
