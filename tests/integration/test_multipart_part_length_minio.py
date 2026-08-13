from __future__ import annotations

from uuid import uuid4

import pytest
import requests

from shared import storage


@pytest.mark.integration
def test_presigned_part_length_is_enforced_and_observable() -> None:
    key = f"integration/multipart-length/{uuid4().hex}.bin"
    expected = b"bounded-part"
    upload_id = storage.create_multipart_upload(key)
    try:
        signed = storage.get_presigned_upload_part_url(
            key,
            upload_id,
            1,
            content_length=len(expected),
        )
        oversized = requests.put(
            signed,
            data=expected + b"oversized",
            timeout=15,
        )
        assert oversized.status_code == 403

        accepted = requests.put(signed, data=expected, timeout=15)
        assert accepted.status_code == 200, accepted.text
        assert storage.list_multipart_parts(key, upload_id) == [
            {
                "PartNumber": 1,
                "Size": len(expected),
                "ETag": accepted.headers["ETag"],
            }
        ]
    finally:
        storage.abort_multipart_upload(key, upload_id)
