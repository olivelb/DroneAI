"""Attempt-scoped in-process cancellation state for Kafka workers."""

from __future__ import annotations

import threading
from collections.abc import Hashable


class AttemptCancellationRegistry:
    """Keep cancellation scoped to one mission/run generation.

    Kafka can deliver work from an older campaign attempt after a retry has
    started. Including the producer's attempt in the key prevents an old
    cancellation from poisoning the new generation.
    """

    def __init__(self) -> None:
        self._cancelled: set[tuple[Hashable, Hashable | None, int]] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _key(
        vol_id: Hashable,
        run_id: Hashable | None,
        attempt: int,
    ) -> tuple[Hashable, Hashable | None, int]:
        return vol_id, run_id, int(attempt)

    def cancel(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
    ) -> None:
        with self._lock:
            self._cancelled.add(self._key(vol_id, run_id, attempt))

    def clear(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
    ) -> None:
        with self._lock:
            self._cancelled.discard(self._key(vol_id, run_id, attempt))

    def is_cancelled(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
    ) -> bool:
        with self._lock:
            return self._key(vol_id, run_id, attempt) in self._cancelled
