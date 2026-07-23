import json

from shared.pipeline_params import normalize_ai_backend
from shared.worker_messaging import make_progress_publisher


class FakeProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, *, key, value):
        self.messages.append((topic, key, json.loads(value)))

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
