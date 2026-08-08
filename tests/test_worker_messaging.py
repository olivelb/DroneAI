import json
from unittest.mock import Mock

from shared.cancellation import AttemptCancellationRegistry
from shared.pipeline_params import normalize_ai_backend
from shared.worker_messaging import (
    make_cancellation_handler,
    make_progress_publisher,
)


class FakeProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, *, key, value, on_delivery=None):
        self.messages.append((topic, key, json.loads(value)))
        if on_delivery is not None:
            self.delivery_callback = on_delivery

    def poll(self, _timeout):
        callback = getattr(self, "delivery_callback", None)
        if callback is not None:
            self.delivery_callback = None
            callback(None, None)
        return 0

    def flush(self):
        return 0


def test_backend_normalization_has_one_shared_policy():
    for alias in ("sam", "SAM3", "sam-3", "meta_sam_3", "segment-anything-3"):
        assert normalize_ai_backend(alias) == "sam3"
    assert normalize_ai_backend(None) == "yolo"
    assert normalize_ai_backend("unknown") == "yolo"


def test_progress_publisher_builds_the_common_status_contract():
    producer = FakeProducer()
    publish = make_progress_publisher(
        producer,
        "pipeline-status",
        service_name="IA",
    )

    publish(
        "mission-1",
        "DETECTING",
        50,
        log="halfway",
        details={"tiles": 4},
    )

    topic, key, event = producer.messages[0]
    assert topic == "pipeline-status"
    assert key == "mission-1"
    assert event["event_type"] == "status"
    assert event["service"] == "IA"
    assert event["step"] == "DETECTING"
    assert event["progress"] == 50
    assert event["log"] == "halfway"
    assert event["details"] == {"tiles": 4}


def test_cancellation_handler_shares_attempt_scoping_across_workers():
    registry = AttemptCancellationRegistry()
    logger = Mock()
    handle = make_cancellation_handler(registry, logger)

    handle({"command": "pause", "vol_id": "mission-1"})
    handle({"command": "cancel"})
    handle(
        {
            "command": "cancel",
            "vol_id": "mission-1",
            "analysis_run_id": "run-2",
            "attempt": 3,
        }
    )

    assert registry.is_cancelled("mission-1", "run-2", 3)
    assert not registry.is_cancelled("mission-1", "run-2", 2)
    logger.info.assert_called_once_with(
        "Cancellation requested for %s analysis=%s",
        "mission-1",
        "run-2",
    )
