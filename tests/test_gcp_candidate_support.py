from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest

support = import_module("app4-dashboard.api.gcp_candidate_support")
routes = import_module("app4-dashboard.api.routers.map_gcp_imports")


@pytest.mark.parametrize(
    ("has_positions", "has_camera_index", "expected"),
    [
        (False, False, None),
        (True, False, "exif-distance"),
        (False, True, "camera-projection"),
        (True, True, "camera-projection+exif-distance"),
    ],
)
def test_candidate_generation_method(has_positions, has_camera_index, expected):
    positions = SimpleNamespace() if has_positions else None
    camera_index = SimpleNamespace() if has_camera_index else None

    assert support.candidate_generation_method(positions, camera_index) == expected


def test_candidate_observation_keeps_source_and_projection_provenance():
    positioned = SimpleNamespace(longitude=1.25, latitude=44.5)
    candidate = support.CandidateSpec(
        image_name="DJI_0001.JPG",
        method="camera-projection",
        distance_m=12.5,
        projected_pixel_x=320.0,
        projected_pixel_y=240.0,
        image_width_px=640,
        image_height_px=480,
        positioned=positioned,
    )

    observation = support.candidate_observation(
        SimpleNamespace(id=7),
        candidate,
        dataset_prefix="datasets/survey/",
        actor_subject="operator-1",
    )

    assert observation.gcp_point_id == 7
    assert observation.image_s3_key == "datasets/survey/DJI_0001.JPG"
    assert observation.candidate_method == "camera-projection"
    assert observation.projected_pixel_x == 320.0
    assert observation.image_longitude == 1.25
    assert observation.created_by == "operator-1"


def test_candidate_refresh_audits_the_number_actually_added(monkeypatch):
    candidate = SimpleNamespace(status="candidate", image_name="old-candidate.jpg")
    marked = SimpleNamespace(status="marked", image_name="operator-mark.jpg")
    point = SimpleNamespace(
        observations=[candidate, marked],
        altitude_m=210.0,
    )
    gcp_set = SimpleNamespace(points=[point])
    mission = SimpleNamespace(
        id=4,
        organization_id="acme",
        workspace_prefix="organizations/acme/missions/demo",
        input_dataset="datasets/demo",
    )

    class FakeSession:
        def __init__(self):
            self.deleted = []
            self.added = []
            self.expire_count = 0

        def delete(self, value):
            self.deleted.append(value)

        def flush(self):
            return None

        def expire(self, value, _attributes):
            self.expire_count += 1
            value.observations = [
                observation
                for observation in value.observations
                if observation.status != "candidate"
            ]

        def add(self, value):
            self.added.append(value)

    session = FakeSession()

    @contextmanager
    def session_scope():
        yield session

    generated = (
        support.CandidateSpec(image_name="new-1.jpg", method="exif-distance"),
        support.CandidateSpec(image_name="new-2.jpg", method="exif-distance"),
    )
    audit = {}
    monkeypatch.setattr(routes, "get_session", session_scope)
    monkeypatch.setattr(routes, "authorized_mission", lambda *_args: mission)
    monkeypatch.setattr(routes, "require_gcp_set", lambda *_args: gcp_set)
    monkeypatch.setattr(
        routes,
        "MissionObjectNamespace",
        SimpleNamespace(from_binding=lambda *_args: SimpleNamespace()),
    )
    monkeypatch.setattr(
        routes,
        "load_mission_image_positions",
        lambda _namespace: SimpleNamespace(),
    )
    monkeypatch.setattr(routes, "load_camera_projection_index", lambda _namespace: None)
    monkeypatch.setattr(routes, "point_longitude_latitude", lambda *_args: (1.2, 44.5))
    monkeypatch.setattr(routes, "rank_candidate_specs", lambda **_kwargs: generated)
    monkeypatch.setattr(
        routes,
        "candidate_observation",
        lambda _point, spec, **_kwargs: SimpleNamespace(image_name=spec.image_name),
    )
    monkeypatch.setattr(
        routes,
        "record_gcp_audit",
        lambda *_args, **kwargs: audit.update(kwargs),
    )
    monkeypatch.setattr(
        routes,
        "set_json",
        lambda session, *_args, **_kwargs: (
            {"set_id": "set-1"}
            if session.expire_count == 2
            else pytest.fail("candidate relationship must be refreshed before serialization")
        ),
    )

    response = routes.refresh_ground_control_candidates(
        "demo",
        "set-1",
        SimpleNamespace(subject="operator-1"),
        candidate_radius_m=250.0,
        max_candidates=20,
    )

    assert session.deleted == [candidate]
    assert [item.image_name for item in session.added] == ["new-1.jpg", "new-2.jpg"]
    assert session.expire_count == 2
    assert audit["before_state"] == {"candidate_count": 1}
    assert audit["after_state"]["candidate_count"] == 2
    assert response["candidate_generation"]["added_observation_count"] == 2
