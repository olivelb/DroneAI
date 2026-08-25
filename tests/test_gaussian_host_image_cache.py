from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training import host_image_cache  # noqa: E402
from gaussian_training.host_image_cache import (  # noqa: E402
    DEFAULT_HOST_IMAGE_CACHE_MIB,
    MAX_HOST_IMAGE_CACHE_MIB,
    HostMemorySnapshot,
    detect_host_memory,
    plan_host_image_cache,
)


def test_auto_cache_uses_available_memory_with_a_system_reserve():
    plan = plan_host_image_cache(
        0,
        memory=HostMemorySnapshot(
            total_mib=96 * 1024,
            available_mib=87 * 1024,
            source="test",
        ),
    )

    assert plan.automatic is True
    assert plan.limit_mib == MAX_HOST_IMAGE_CACHE_MIB
    assert plan.reserve_mib == 19_661
    assert plan.source == "test"


def test_auto_cache_scales_down_on_a_bounded_host():
    plan = plan_host_image_cache(
        0,
        memory=HostMemorySnapshot(
            total_mib=32 * 1024,
            available_mib=24 * 1024,
            source="test",
        ),
    )

    assert plan.limit_mib == 16 * 1024
    assert plan.reserve_mib == 8 * 1024


def test_auto_cache_falls_back_when_linux_memory_is_unavailable(monkeypatch):
    monkeypatch.setattr(host_image_cache, "detect_host_memory", lambda: None)
    plan = plan_host_image_cache(0, memory=None)

    assert plan.source == "fallback-default"
    assert plan.limit_mib == DEFAULT_HOST_IMAGE_CACHE_MIB


@pytest.mark.parametrize("value", [-1, 255, 65_537, True, "auto"])
def test_cache_rejects_invalid_explicit_values(value):
    with pytest.raises(ValueError, match="host image cache"):
        plan_host_image_cache(value)  # type: ignore[arg-type]


def test_explicit_cache_is_preserved():
    plan = plan_host_image_cache(12_288)

    assert plan.automatic is False
    assert plan.limit_mib == 12_288
    assert plan.source == "explicit"


def test_cgroup_headroom_limits_host_memory(tmp_path):
    meminfo = tmp_path / "meminfo"
    memory_max = tmp_path / "memory.max"
    memory_current = tmp_path / "memory.current"
    meminfo.write_text(
        "MemTotal:       134217728 kB\nMemAvailable:   104857600 kB\n",
        encoding="utf-8",
    )
    memory_max.write_text(str(32 * 1024**3), encoding="utf-8")
    memory_current.write_text(str(8 * 1024**3), encoding="utf-8")

    snapshot = detect_host_memory(
        meminfo_path=meminfo,
        cgroup_limit_path=memory_max,
        cgroup_current_path=memory_current,
    )

    assert snapshot == HostMemorySnapshot(
        total_mib=32 * 1024,
        available_mib=24 * 1024,
        source="proc-meminfo+cgroup-v2",
    )
