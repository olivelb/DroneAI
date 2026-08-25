"""Resolve a bounded host image-cache ceiling from Linux memory headroom."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
MIN_HOST_IMAGE_CACHE_MIB = 256
DEFAULT_HOST_IMAGE_CACHE_MIB = 2_048
MAX_HOST_IMAGE_CACHE_MIB = 65_536
SMALL_HOST_RESERVE_CAP_MIB = 16_384


@dataclass(frozen=True)
class HostMemorySnapshot:
    """Effective memory visible to the trainer after cgroup constraints."""

    total_mib: int
    available_mib: int
    source: str


@dataclass(frozen=True)
class HostImageCachePlan:
    """Requested cache policy and the explicit ceiling sent to DroneGS."""

    requested_mib: int
    limit_mib: int
    automatic: bool
    total_mib: int | None
    available_mib: int | None
    reserve_mib: int | None
    source: str


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_meminfo_mib(text: str | None, key: str) -> int | None:
    if not text:
        return None
    for line in text.splitlines():
        name, separator, raw_value = line.partition(":")
        if separator and name == key:
            fields = raw_value.split()
            if not fields:
                return None
            try:
                kib = int(fields[0])
            except ValueError:
                return None
            return max(0, kib // 1024)
    return None


def _parse_cgroup_bytes(text: str | None) -> int | None:
    if not text or text == "max":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def detect_host_memory(
    *,
    meminfo_path: Path = Path("/proc/meminfo"),
    cgroup_limit_path: Path = Path("/sys/fs/cgroup/memory.max"),
    cgroup_current_path: Path = Path("/sys/fs/cgroup/memory.current"),
) -> HostMemorySnapshot | None:
    """Return host memory clipped to the active cgroup-v2 headroom."""

    meminfo = _read_text(meminfo_path)
    host_total = _parse_meminfo_mib(meminfo, "MemTotal")
    host_available = _parse_meminfo_mib(meminfo, "MemAvailable")
    cgroup_limit_bytes = _parse_cgroup_bytes(_read_text(cgroup_limit_path))
    cgroup_current_bytes = _parse_cgroup_bytes(_read_text(cgroup_current_path))

    total_candidates = [value for value in (host_total,) if value is not None]
    available_candidates = [value for value in (host_available,) if value is not None]
    source = "proc-meminfo"
    if cgroup_limit_bytes is not None:
        cgroup_limit_mib = cgroup_limit_bytes // MIB
        total_candidates.append(cgroup_limit_mib)
        if cgroup_current_bytes is not None:
            cgroup_available_mib = max(0, cgroup_limit_bytes - cgroup_current_bytes) // MIB
            available_candidates.append(cgroup_available_mib)
        source = "proc-meminfo+cgroup-v2"

    if not total_candidates or not available_candidates:
        return None
    total_mib = min(total_candidates)
    available_mib = min(min(available_candidates), total_mib)
    if total_mib <= 0:
        return None
    return HostMemorySnapshot(
        total_mib=total_mib,
        available_mib=max(0, available_mib),
        source=source,
    )


def _system_reserve_mib(total_mib: int) -> int:
    # Reserve 25% on smaller hosts, smoothly capped at 16 GiB, and at least
    # 20% on larger hosts. This leaves room for COLMAP/Python/CuPy and the OS.
    quarter = (total_mib + 3) // 4
    fifth = (total_mib + 4) // 5
    return max(min(SMALL_HOST_RESERVE_CAP_MIB, quarter), fifth)


def plan_host_image_cache(
    requested_mib: int,
    *,
    memory: HostMemorySnapshot | None = None,
) -> HostImageCachePlan:
    """Resolve ``0`` (auto) or validate an explicit DroneGS cache ceiling."""

    if not isinstance(requested_mib, int) or isinstance(requested_mib, bool):
        raise ValueError("host image cache must be an integer MiB value")
    if requested_mib != 0:
        if not MIN_HOST_IMAGE_CACHE_MIB <= requested_mib <= MAX_HOST_IMAGE_CACHE_MIB:
            raise ValueError("host image cache must be 0 (auto) or between 256 and 65536 MiB")
        return HostImageCachePlan(
            requested_mib=requested_mib,
            limit_mib=requested_mib,
            automatic=False,
            total_mib=None,
            available_mib=None,
            reserve_mib=None,
            source="explicit",
        )

    snapshot = memory or detect_host_memory()
    if snapshot is None:
        return HostImageCachePlan(
            requested_mib=0,
            limit_mib=DEFAULT_HOST_IMAGE_CACHE_MIB,
            automatic=True,
            total_mib=None,
            available_mib=None,
            reserve_mib=None,
            source="fallback-default",
        )
    reserve_mib = _system_reserve_mib(snapshot.total_mib)
    budget_mib = max(MIN_HOST_IMAGE_CACHE_MIB, snapshot.available_mib - reserve_mib)
    limit_mib = min(MAX_HOST_IMAGE_CACHE_MIB, budget_mib)
    return HostImageCachePlan(
        requested_mib=0,
        limit_mib=limit_mib,
        automatic=True,
        total_mib=snapshot.total_mib,
        available_mib=snapshot.available_mib,
        reserve_mib=reserve_mib,
        source=snapshot.source,
    )
