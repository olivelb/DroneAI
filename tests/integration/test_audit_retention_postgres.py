"""Real PostgreSQL regression for artifact deletion fencing and completed Job cleanup."""
import importlib
from uuid import uuid4
import pytest
from sqlalchemy.exc import IntegrityError
from shared.database import Mission, MissionStageRun, MissionArtifact, MissionArtifactParent, Organization, get_session
from shared.tenancy import mission_prefix

pytestmark = pytest.mark.integration


def test_deleting_mission_rejects_late_artifact_parent_reference():
    org = "audit-" + uuid4().hex[:12]
    with get_session() as session:
        session.add(Organization(id=org, display_name=org, status="active", created_by="test", updated_by="test"))
        session.flush()
        missions = []
        artifacts = []
        for number in range(2):
            mission = Mission(organization_id=org, vol_id=f"mission-{number}", owner_subject="test", status="completed", workspace_prefix=mission_prefix(org, f"mission-{number}"))
            session.add(mission)
            session.flush()
            run = MissionStageRun(run_id=uuid4().hex, mission_id=mission.id, stage="reconstruction", status="succeeded", idempotency_key=uuid4().hex * 2, executor="kubernetes-job", job_name="audit-" + uuid4().hex)
            session.add(run)
            session.flush()
            artifact = MissionArtifact(artifact_id=uuid4().hex, mission_id=mission.id, stage_run_id=run.id, kind="reconstruction_workspace", uri="s3://test/manifest.json", checksum_sha256="a" * 64, size_bytes=1)
            session.add(artifact)
            session.flush()
            missions.append(mission.id)
            artifacts.append(artifact.id)
    with get_session() as session:
        session.get(Mission, missions[0]).status = "deleting"
    with pytest.raises(IntegrityError, match="deleting mission"):
        with get_session() as session:
            session.add(MissionArtifactParent(artifact_id=artifacts[1], parent_artifact_id=artifacts[0]))

    orchestrator = importlib.import_module("app4-dashboard.api.stage_orchestrator")
    class FakeJobClient:
        def get(self, name):
            raise orchestrator.KubernetesApiError(404, "gone")
        def pods_for_job(self, name):
            return []
    from types import SimpleNamespace
    def _settings():
        return SimpleNamespace(maximum_dispatch_attempts=3)
    orchestrator.reconcile_stage_jobs(FakeJobClient(), _settings())
    with get_session() as session:
        for run in session.query(MissionStageRun).filter(MissionStageRun.mission_id.in_(missions)):
            assert run.provenance["cleanup_requested_at"]
            assert run.provenance["cleanup_confirmed_at"]
