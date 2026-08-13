"""Black-box HTTP journey through real API, control worker, DB, Kafka and S3."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
import requests


API_URL = os.getenv("DRONEAI_HTTP_E2E_URL", "").rstrip("/")
API_KEY = os.getenv("DRONEAI_HTTP_E2E_API_KEY", "")
ORGANIZATION_ID = "http-e2e"


def _api(
    method: str,
    path: str,
    *,
    expected: int = 200,
    **kwargs: object,
) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        **dict(kwargs.pop("headers", {})),
    }
    response = requests.request(
        method,
        f"{API_URL}{path}",
        headers=headers,
        timeout=15,
        **kwargs,
    )
    assert response.status_code == expected, response.text
    return response


@pytest.mark.integration
@pytest.mark.skipif(
    not API_URL or not API_KEY,
    reason="HTTP control-plane composition is not running",
)
def test_launch_and_cancel_mission_through_real_http_control_plane() -> None:
    unique = uuid4().hex[:16]
    dataset_name = f"http-e2e-{unique}"
    mission_id = f"http-e2e-{unique}"
    upload_session_id = None
    mission_created = False
    dataset_created = False

    assert requests.get(f"{API_URL}/live", timeout=10).json() == {"status": "ok"}
    assert requests.get(f"{API_URL}/ready", timeout=10).json() == {
        "status": "ok",
        "bootstrap_credentials_active": True,
    }
    assert requests.get(f"{API_URL}/missions", timeout=10).status_code == 401

    try:
        bootstrap = _api(
            "POST",
            "/auth/bootstrap",
            expected=201,
            json={"display_name": "HTTP E2E organization"},
        ).json()
        assert bootstrap["organization"]["id"] == ORGANIZATION_ID
        assert bootstrap["member"]["subject"] == "http-e2e-admin"

        content = b"\xff\xd8\xff\xd9"
        upload = _api(
            "POST",
            "/datasets/upload-sessions",
            expected=201,
            json={
                "dataset_name": dataset_name,
                "files": [
                    {
                        "name": "synthetic.jpg",
                        "size": len(content),
                        "content_type": "image/jpeg",
                    }
                ],
            },
        ).json()
        upload_session_id = upload["session_id"]
        file_record = upload["files"][0]
        part = _api(
            "POST",
            (
                f"/datasets/upload-sessions/{upload_session_id}/files/"
                f"{file_record['file_id']}/parts/1"
            ),
        ).json()
        uploaded = requests.put(
            part["url"],
            data=content,
            headers={"Content-Type": "image/jpeg"},
            timeout=15,
        )
        uploaded.raise_for_status()
        etag = uploaded.headers["ETag"]
        _api(
            "POST",
            (
                f"/datasets/upload-sessions/{upload_session_id}/files/"
                f"{file_record['file_id']}/complete"
            ),
            json={"parts": [{"part_number": 1, "etag": etag}]},
        )
        finalized = _api(
            "POST",
            f"/datasets/upload-sessions/{upload_session_id}/complete",
        ).json()
        assert finalized["status"] == "done"
        dataset_created = True

        datasets = _api("GET", "/datasets").json()
        dataset = next(item for item in datasets if item["name"] == dataset_name)
        assert dataset["path"].startswith(
            f"organizations/{ORGANIZATION_ID}/datasets/"
        )
        capacity = _api(
            "GET",
            "/operations/organization/capacity",
        ).json()
        assert capacity["organization_id"] == ORGANIZATION_ID
        assert capacity["policy"]["configured"] is False
        assert capacity["usage"]["storage_bytes"] == len(content)
        usage_events = _api(
            "GET",
            "/operations/organization/usage-events",
        ).json()
        assert any(
            item["action"] == "storage_reserved"
            and item["quantity"] == len(content)
            for item in usage_events
        )

        started = _api(
            "POST",
            "/mission",
            json={
                "vol_id": mission_id,
                "input_dataset": dataset["path"],
                "phases": ["reconstruction"],
            },
        ).json()
        assert started == {"status": "success", "vol_id": mission_id}
        mission_created = True

        cancelled = _api(
            "POST",
            f"/mission/cancel?vol_id={mission_id}",
        ).json()
        assert cancelled["status"] == "success"
        detail = _api("GET", f"/missions/{mission_id}").json()
        assert detail["status"] == "cancelled"
        assert detail["owner_subject"] == "http-e2e-admin"

        delivery = None
        deadline = time.monotonic() + 20
        expected_key = f"{ORGANIZATION_ID}:{mission_id}"
        while delivery is None and time.monotonic() < deadline:
            records = _api("GET", "/operations/outbox?limit=100").json()
            delivery = next(
                (
                    item
                    for item in records
                    if item["event_type"] == "control"
                    and item["message_key"] == expected_key
                    and item["status"] == "published"
                ),
                None,
            )
            if delivery is None:
                time.sleep(0.25)
        assert delivery is not None, "Control worker did not publish cancellation"
        assert delivery["topic"] == "pipeline-control"
        assert delivery["attempts"] == 1
        assert delivery["published_at"]
        assert _api("GET", "/operations/outbox/dead").json() == []
    finally:
        if mission_created:
            deletion = _api(
                "DELETE",
                f"/mission/{mission_id}",
                expected=202,
            ).json()
            assert deletion["deletion_pending"] is True
        if dataset_created:
            _api("DELETE", f"/datasets/{dataset_name}")
        elif upload_session_id is not None:
            _api(
                "DELETE",
                f"/datasets/upload-sessions/{upload_session_id}",
            )
