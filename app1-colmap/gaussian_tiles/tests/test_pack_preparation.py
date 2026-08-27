from __future__ import annotations

import sys
from pathlib import Path
from threading import Event, get_ident

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles.pack_preparation import OrderedPackPreparation  # noqa: E402


def test_out_of_order_workers_commit_in_order_on_owner_thread():
    release = Event()
    owner = get_ident()
    commits = []
    queue = OrderedPackPreparation[int](2, 100)
    def first():
        assert release.wait(5)
        return 1
    def second():
        release.set()
        return 2
    try:
        queue.submit(10, first, lambda value: commits.append((value, get_ident())))
        queue.submit(10, second, lambda value: commits.append((value, get_ident())))
        queue.drain()
        assert commits == [(1, owner), (2, owner)]
        assert queue.peak_tasks == 2
        assert queue.peak_bytes == 20
    finally:
        release.set()
        queue.close()


def test_bytes_force_drain_before_input_allocation():
    commits = []
    queue = OrderedPackPreparation[int](4, 100)
    try:
        queue.submit(60, lambda: 1, commits.append)
        queue.make_room(60)
        assert commits == [1]
        queue.submit(60, lambda: 2, commits.append)
        queue.drain()
        assert commits == [1, 2]
        assert queue.peak_bytes == 60
    finally:
        queue.close()


def test_count_limit_and_oversize_fallback_are_bounded():
    owner = get_ident()
    commits = []
    queue = OrderedPackPreparation[int](2, 100)
    try:
        for value in range(3):
            queue.submit(10, lambda value=value: value, commits.append)
        assert commits == [0]
        queue.submit(101, get_ident, lambda thread: commits.append(thread))
        assert commits == [0, 1, 2, owner]
        assert queue.peak_tasks == 2
        assert queue.peak_bytes == 20
        assert queue.oversized_inline_tasks == 1
    finally:
        queue.close()
    with pytest.raises(RuntimeError, match="closed"):
        queue.submit(1, lambda: 1, commits.append)


def test_cancellation_joins_running_worker_without_committing():
    started, release, stopped = Event(), Event(), Event()
    armed = False
    commits = []
    def cancel():
        if armed:
            release.set()
            raise RuntimeError("cancelled")
    def prepare():
        started.set()
        assert release.wait(5)
        stopped.set()
        return 1
    queue = OrderedPackPreparation[int](2, 100, cancel)
    try:
        queue.submit(10, prepare, commits.append)
        assert started.wait(5)
        armed = True
        with pytest.raises(RuntimeError, match="cancelled"):
            queue.drain()
    finally:
        release.set()
        queue.close()
    assert stopped.is_set()
    assert commits == []
    assert queue.pending_bytes == 0


@pytest.mark.parametrize("error", [ValueError("encode failed"), TimeoutError("worker timeout")])
def test_worker_exception_is_not_retried(error):
    queue = OrderedPackPreparation[int](2, 100)
    commits = []
    def fail():
        raise error
    try:
        queue.submit(10, fail, commits.append)
        with pytest.raises(type(error), match=str(error)):
            queue.drain()
    finally:
        queue.close()
    assert commits == []


def test_writer_failure_does_not_commit_later_jobs():
    queue = OrderedPackPreparation[int](2, 100)
    commits = []
    def fail(value):
        raise OSError("disk full")
    try:
        queue.submit(10, lambda: 1, fail)
        queue.submit(10, lambda: 2, commits.append)
        with pytest.raises(OSError, match="disk full"):
            queue.drain()
    finally:
        queue.close()
    assert commits == []
