"""Cancellation support for bounded COLMAP stages."""

import threading

from shared.cancellation import AttemptCancellationRegistry


class WorkerCancellationState:
    def __init__(self, registry=None):
        self._lock = threading.Lock()
        self._registry = registry or AttemptCancellationRegistry()
        self._current_mission_id = None
        self._current_organization_id = None
        self._current_attempt = 0
        self._cancel_requested = False

    def start_mission(self, vol_id, attempt=0, organization_id=None):
        with self._lock:
            self._current_mission_id = vol_id
            self._current_organization_id = organization_id
            self._current_attempt = int(attempt)
            self._cancel_requested = self._registry.is_cancelled(
                vol_id,
                None,
                attempt,
                organization_id=organization_id,
            )

    def clear(self):
        with self._lock:
            vol_id = self._current_mission_id
            attempt = self._current_attempt
            organization_id = self._current_organization_id
            self._current_mission_id = None
            self._current_organization_id = None
            self._current_attempt = 0
            self._cancel_requested = False
        if vol_id is not None:
            self._registry.clear(
                vol_id,
                None,
                attempt,
                organization_id=organization_id,
            )

    def should_cancel(self, vol_id):
        with self._lock:
            return self._current_mission_id == vol_id

    def on_cancel(self, vol_id):
        with self._lock:
            attempt = self._current_attempt
            organization_id = self._current_organization_id
        self.cancel(
            vol_id,
            None,
            attempt,
            organization_id=organization_id,
        )

    def cancel(self, vol_id, run_id=None, attempt=0, *, organization_id=None):
        self._registry.cancel(
            vol_id,
            run_id,
            attempt,
            organization_id=organization_id,
        )
        with self._lock:
            if (
                run_id is None
                and self._current_mission_id == vol_id
                and self._current_attempt == int(attempt)
                and self._current_organization_id == organization_id
            ):
                self._cancel_requested = True
        print(f"⚠️ Cancel requested for {vol_id}")

    def is_cancel_requested(self):
        with self._lock:
            if self._cancel_requested:
                return True
            vol_id = self._current_mission_id
            attempt = self._current_attempt
            organization_id = self._current_organization_id
        if vol_id is None:
            return False
        cancelled = self._registry.is_cancelled(
            vol_id,
            None,
            attempt,
            organization_id=organization_id,
        )
        if cancelled:
            with self._lock:
                if (
                    self._current_mission_id == vol_id
                    and self._current_attempt == attempt
                ):
                    self._cancel_requested = True
        return cancelled

    def ensure_not_cancelled(self, process=None):
        if not self.is_cancel_requested():
            return
        if process is not None:
            process.kill()
        raise RuntimeError("Mission cancelled by user")
