from __future__ import annotations

import importlib
import threading
from contextlib import contextmanager

import pytest


control_runtime = importlib.import_module("app4-dashboard.api.control_runtime")
control_worker = importlib.import_module("app4-dashboard.api.control_worker")
health = importlib.import_module("app4-dashboard.api.health")
main = importlib.import_module("app4-dashboard.api.main")


def test_embedded_control_loop_configuration_is_strict(monkeypatch):
    monkeypatch.delenv("DRONEAI_EMBED_CONTROL_LOOPS", raising=False)
    assert control_runtime.embedded_control_loops_enabled() is True
    monkeypatch.setenv("DRONEAI_EMBED_CONTROL_LOOPS", "false")
    assert control_runtime.embedded_control_loops_enabled() is False
    monkeypatch.setenv("DRONEAI_EMBED_CONTROL_LOOPS", "sometimes")
    with pytest.raises(RuntimeError, match="must be true or false"):
        control_runtime.embedded_control_loops_enabled()


def test_control_supervisor_starts_and_stops_every_loop(monkeypatch):
    observed: list[str] = []
    observed_lock = threading.Lock()
    all_started = threading.Event()

    def wait_for_stop(name, stop_event):
        with observed_lock:
            observed.append(name)
            if len(observed) == 4:
                all_started.set()
        stop_event.wait()

    def outbox(_session_scope, *, stop_event, **_kwargs):
        wait_for_stop("outbox", stop_event)

    def uploads(stop_event):
        wait_for_stop("uploads", stop_event)

    def retention(stop_event):
        wait_for_stop("retention", stop_event)

    def stage_starter(stop_event):
        thread = threading.Thread(
            target=wait_for_stop,
            args=("stage", stop_event),
            daemon=True,
            name="stage-orchestrator-test",
        )
        thread.start()
        return thread

    monkeypatch.setattr(control_runtime, "run_outbox_dispatcher", outbox)
    monkeypatch.setattr(control_runtime.dataset_uploads, "run_upload_cleanup", uploads)
    monkeypatch.setattr(control_runtime, "run_retention_cleanup", retention)
    monkeypatch.setattr(control_runtime, "start_stage_orchestrator", stage_starter)

    supervisor = control_runtime.start_control_loops()
    try:
        assert {thread.name for thread in supervisor.threads} == {
            "outbox-dispatcher",
            "dataset-upload-reconciler",
            "organization-retention",
            "stage-orchestrator-test",
        }
        assert all_started.wait(1)
        assert set(observed) == {"outbox", "uploads", "retention", "stage"}
        supervisor.raise_if_unhealthy()
    finally:
        supervisor.stop()
    assert all(not thread.is_alive() for thread in supervisor.threads)


def test_control_supervisor_rejects_protected_fused_mode_before_threads_start(
    monkeypatch,
):
    started = []
    monkeypatch.setenv("DRONEAI_ENV", "staging")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")
    monkeypatch.setattr(
        control_runtime.threading.Thread,
        "start",
        lambda self: started.append(self.name),
    )

    with pytest.raises(RuntimeError, match="require bounded stage Jobs"):
        control_runtime.start_control_loops()

    assert started == []


def test_control_supervisor_reports_an_unexpected_exit():
    thread = threading.Thread(target=lambda: None, name="exited-loop")
    thread.start()
    thread.join()
    supervisor = control_runtime.ControlLoopSupervisor(
        threading.Event(),
        (thread,),
    )
    with pytest.raises(RuntimeError, match="exited-loop"):
        supervisor.raise_if_unhealthy()


def test_elected_worker_waits_then_runs_and_releases_leadership(monkeypatch):
    process_stop = threading.Event()
    acquisition_attempts = []

    class Leadership:
        released = False

        def raise_if_unhealthy(self):
            return None

        def release(self):
            self.released = True

    lease = Leadership()

    def acquire():
        acquisition_attempts.append(True)
        return None if len(acquisition_attempts) == 1 else lease

    class Supervisor:
        stopped = False

        def raise_if_unhealthy(self):
            process_stop.set()

        def stop(self):
            self.stopped = True

    supervisor = Supervisor()
    monkeypatch.setattr(
        control_worker,
        "try_acquire_control_leadership",
        acquire,
    )
    monkeypatch.setattr(
        control_worker,
        "start_control_loops",
        lambda _stop_event: supervisor,
    )

    control_worker._run_elected_loops(process_stop, 0.001)

    assert len(acquisition_attempts) == 2
    assert supervisor.stopped is True
    assert lease.released is True


def test_database_readiness_executes_a_real_query(monkeypatch):
    statements = []

    class Session:
        def execute(self, statement):
            statements.append(str(statement))

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setattr(health, "get_session", session_scope)
    assert health.database_is_ready() is True
    assert statements == ["SELECT 1"]


def test_database_readiness_fails_closed(monkeypatch):
    @contextmanager
    def failed_session_scope():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(health, "get_session", failed_session_scope)
    assert health.database_is_ready() is False


def test_database_readiness_requires_active_rls_when_configured(monkeypatch):
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class Session:
        def execute(self, statement):
            return Result("row_security_active" in str(statement))

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setenv("DRONEAI_RLS_REQUIRED", "true")
    monkeypatch.setattr(health, "get_session", session_scope)
    assert health.database_is_ready() is True

    Session.execute = lambda _self, _statement: Result(False)
    assert health.database_is_ready() is False


def test_http_probes_distinguish_liveness_from_readiness(monkeypatch):
    routes = {
        route.path: route.endpoint
        for route in main.app.routes
        if hasattr(route, "path")
    }
    assert routes["/live"]() == {"status": "ok"}
    monkeypatch.setattr(main, "database_is_ready", lambda: True)
    assert routes["/ready"]() == {
        "status": "ok",
        "bootstrap_credentials_active": False,
    }
    monkeypatch.setattr(main, "database_is_ready", lambda: False)
    unavailable = routes["/ready"]()
    assert unavailable.status_code == 503
    assert unavailable.body == (
        b'{"status":"unavailable","bootstrap_credentials_active":false}'
    )
