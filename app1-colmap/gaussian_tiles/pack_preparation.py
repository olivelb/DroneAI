"""Ordered, byte-bounded CPU preparation; workers never publish files."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class OrderedPackPreparation(Generic[T]):
    """Single-owner queue; callbacks always execute on the caller's thread."""

    def __init__(self, workers: int, maximum_bytes: int, cancellation_check: Callable[[], None] | None = None):
        if type(workers) is not int or workers not in (1, 2, 4):
            raise ValueError("pack_workers must be 1, 2 or 4")
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            raise ValueError("pack preparation byte limit must be positive")
        self.workers = workers
        self.maximum_bytes = maximum_bytes
        self.cancellation_check = cancellation_check
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gstile-pack") if workers > 1 else None
        self.pending: deque[tuple[Future[T], Callable[[T], None], int]] = deque()
        self.pending_bytes = 0
        self.peak_bytes = 0
        self.peak_tasks = 0
        self.oversized_inline_tasks = 0
        self.closed = False

    def _check(self) -> None:
        if self.closed:
            raise RuntimeError("GSTile pack preparation is closed")
        if self.cancellation_check is not None:
            self.cancellation_check()

    def make_room(self, reserved_bytes: int) -> None:
        """Call before copying owned inputs; submit rechecks admission."""
        self._check()
        if type(reserved_bytes) is not int or reserved_bytes < 1:
            raise ValueError("Invalid GSTile pack reservation")
        while self.pending and (len(self.pending) >= self.workers or
                                self.pending_bytes + reserved_bytes > self.maximum_bytes):
            self._consume_one()

    def submit(self, reserved_bytes: int, prepare: Callable[[], T], commit: Callable[[T], None]) -> None:
        self.make_room(reserved_bytes)
        if self.executor is None or reserved_bytes > self.maximum_bytes:
            # One unusually large tile must remain supported, without retaining
            # other queued payloads or pretending the queue cap bounds its scratch.
            self.oversized_inline_tasks += int(reserved_bytes > self.maximum_bytes)
            value = prepare()
            self._check()
            commit(value)
            return
        future = self.executor.submit(prepare)
        self.pending.append((future, commit, reserved_bytes))
        self.pending_bytes += reserved_bytes
        self.peak_bytes = max(self.peak_bytes, self.pending_bytes)
        self.peak_tasks = max(self.peak_tasks, len(self.pending))

    def _consume_one(self) -> None:
        future, commit, reserved_bytes = self.pending[0]
        while True:
            self._check()
            try:
                result = future.result(timeout=0.05)
                break
            except TimeoutError:
                # A task can itself raise TimeoutError; don't retry that failure.
                if future.done():
                    result = future.result()
                    break
        self._check()
        self.pending.popleft()
        self.pending_bytes -= reserved_bytes
        commit(result)

    def drain(self) -> None:
        self._check()
        while self.pending:
            self._consume_one()

    def close(self) -> None:
        """Cancel queued tasks, join running preparation, never commit on error."""
        self.closed = True
        if self.executor is not None:
            self.executor.shutdown(wait=True, cancel_futures=True)
        self.pending.clear()
        self.pending_bytes = 0
