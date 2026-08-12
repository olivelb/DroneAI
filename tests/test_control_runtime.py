from __future__ import annotations

import importlib
import threading
from contextlib import contextmanager

import pytest


control_runtime = importlib.import_module("app4-dashboard.api.control_runtime")
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
            if len(observed) == 3:
                all_started.set()
        stop_event.wait()

    def outbox(_session_scope, *, stop_event, **_kwargs):
        wait_for_stop("outbox", stop_event)

    def uploads(stop_event):
        wait_for_stop("uploads", stop_event)

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
    monkeypatch.setattr(control_runtime, "start_stage_orchestrator", stage_starter)

    supervisor = control_runtime.start_control_loops()
    try:
        assert {thread.name for thread in supervisor.threads} == {
            "outbox-dispatcher",
            "dataset-upload-reconciler",
            "stage-orchestrator-test",
        }
        assert all_started.wait(1)
        assert set(observed) == {"outbox", "uploads", "stage"}
        supervisor.raise_if_unhealthy()
    finally:
        supervisor.stop()
    assert all(not thread.is_alive() for thread in supervisor.threads)


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


def test_http_probes_distinguish_liveness_from_readiness(monkeypatch):
    routes = {
        route.path: route.endpoint
        for route in main.app.routes
        if hasattr(route, "path")
    }
    assert routes["/live"]() == {"status": "ok"}
    monkeypatch.setattr(main, "database_is_ready", lambda: True)
    assert routes["/ready"]() == {"status": "ok"}
    monkeypatch.setattr(main, "database_is_ready", lambda: False)
    unavailable = routes["/ready"]()
    assert unavailable.status_code == 503
