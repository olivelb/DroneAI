"""Repeatable subprocess benchmark harness for Gaussian trainers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SUITE_SCHEMA_VERSION = 1
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
REQUIRED_PARAMETERS = {
    "iterations", "strategy", "sh_degree", "max_cap",
    "resize_factor", "max_width", "tile_mode", "seed",
}
PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
ENV_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class BenchmarkBackend:
    name: str
    command: tuple[str, ...]
    required_artifact_glob: str = "**/*.ply"


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    data_path: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class BenchmarkSuite:
    name: str
    repetitions: int
    backends: tuple[BenchmarkBackend, ...]
    cases: tuple[BenchmarkCase, ...]
    schema_version: int = SUITE_SCHEMA_VERSION


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if any(char in value for char in ("/", "\\")) or ".." in value:
        raise ValueError(f"{label} must be a safe path component")
    return value.strip()


def _validate_parameters(parameters: Mapping[str, Any], case_name: str) -> None:
    def integer(name: str, minimum: int, allowed: set[int] | None = None) -> int:
        value = parameters.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f"case {case_name!r} parameter {name} is invalid")
        if allowed is not None and value not in allowed:
            raise ValueError(f"case {case_name!r} parameter {name} is invalid")
        return value

    integer("iterations", 1)
    integer("sh_degree", 0, {0, 1, 2, 3})
    integer("max_cap", 1)
    integer("resize_factor", 1, {1, 2, 4, 8})
    if integer("max_width", 1) > 4096:
        raise ValueError(f"case {case_name!r} parameter max_width is invalid")
    integer("tile_mode", 1, {1, 2, 4})
    integer("seed", 0)
    if parameters.get("strategy") != "mrnf":
        raise ValueError(
            f"case {case_name!r} parameter strategy must be mrnf"
        )


def load_benchmark_suite(path: Path) -> BenchmarkSuite:
    """Load and strictly validate a benchmark suite JSON file."""

    payload = _require_mapping(json.loads(path.read_text(encoding="utf-8")), "suite")
    schema_version = payload.get("schema_version")
    if schema_version != SUITE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {schema_version!r}; expected {SUITE_SCHEMA_VERSION}"
        )
    name = _require_name(payload.get("name"), "suite.name")
    repetitions = payload.get("repetitions", 5)
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("suite.repetitions must be a positive integer")

    raw_backends = payload.get("backends")
    if not isinstance(raw_backends, list) or not raw_backends:
        raise ValueError("suite.backends must be a non-empty array")
    backends: list[BenchmarkBackend] = []
    for index, raw_backend in enumerate(raw_backends):
        backend = _require_mapping(raw_backend, f"suite.backends[{index}]")
        backend_name = _require_name(backend.get("name"), f"suite.backends[{index}].name")
        command = backend.get("command")
        if (
            not isinstance(command, list) or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError(f"backend {backend_name!r} command must be a string array")
        artifact_glob = backend.get("required_artifact_glob", "**/*.ply")
        if not isinstance(artifact_glob, str) or not artifact_glob:
            raise ValueError(f"backend {backend_name!r} artifact glob is invalid")
        backends.append(BenchmarkBackend(backend_name, tuple(command), artifact_glob))
    if len({backend.name for backend in backends}) != len(backends):
        raise ValueError("backend names must be unique")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("suite.cases must be a non-empty array")
    cases: list[BenchmarkCase] = []
    for index, raw_case in enumerate(raw_cases):
        case = _require_mapping(raw_case, f"suite.cases[{index}]")
        case_name = _require_name(case.get("name"), f"suite.cases[{index}].name")
        data_path = case.get("data_path")
        if not isinstance(data_path, str) or not data_path:
            raise ValueError(f"case {case_name!r} data_path must be a string")
        parameters = _require_mapping(case.get("parameters"), f"case {case_name!r}.parameters")
        missing = sorted(REQUIRED_PARAMETERS - set(parameters))
        if missing:
            raise ValueError(f"case {case_name!r} is missing parameters: {', '.join(missing)}")
        _validate_parameters(parameters, case_name)
        cases.append(BenchmarkCase(case_name, data_path, dict(parameters)))
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("case names must be unique")
    return BenchmarkSuite(name, repetitions, tuple(backends), tuple(cases), schema_version)


def expand_environment(value: str, environment: Mapping[str, str] | None = None) -> str:
    """Expand ${NAME} while failing on missing variables."""

    environment = environment or os.environ

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment:
            raise ValueError(f"environment variable {name} is required")
        return environment[name]

    return ENV_PATTERN.sub(replace, value)


def expand_command(
    backend: BenchmarkBackend,
    case: BenchmarkCase,
    output_path: Path,
    repetition: int,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Expand environment and canonical placeholders into argv."""

    values: dict[str, Any] = dict(case.parameters)
    values.update(
        data_path=expand_environment(case.data_path, environment),
        output_path=str(output_path),
        run_manifest=str(output_path / "trainer_run.json"),
        repetition=repetition,
        seed=int(case.parameters["seed"]) + repetition - 1,
    )
    argv: list[str] = []
    for raw_part in backend.command:
        part = expand_environment(raw_part, environment)
        unknown = sorted(set(PLACEHOLDER_PATTERN.findall(part)) - set(values))
        if unknown:
            raise ValueError(
                f"backend {backend.name!r} uses unknown placeholders: {', '.join(unknown)}"
            )
        argv.append(part.format_map({key: str(value) for key, value in values.items()}))
    return argv


def dataset_inventory(path: Path) -> dict[str, Any]:
    """Fingerprint relative paths/sizes and full sparse-model contents."""

    root = path.resolve()
    if not root.is_dir():
        raise ValueError(f"dataset path is not a directory: {root}")
    digest = hashlib.sha256()
    image_count = 0
    image_bytes = 0
    file_count = 0
    sparse_names = {"cameras.bin", "images.bin", "points3D.bin"}
    files = sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    for file_path in files:
        relative = file_path.relative_to(root).as_posix()
        size = file_path.stat().st_size
        file_count += 1
        digest.update(relative.encode("utf-8") + b"\0" + str(size).encode("ascii") + b"\0")
        if file_path.suffix.lower() in IMAGE_SUFFIXES:
            image_count += 1
            image_bytes += size
        if file_path.name in sparse_names:
            with file_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return {
        "path": str(root),
        "fingerprint_kind": "inventory-size-plus-sparse-content-v1",
        "fingerprint": digest.hexdigest(),
        "file_count": file_count,
        "image_count": image_count,
        "image_bytes": image_bytes,
    }


def read_ply_vertex_count(path: Path) -> int | None:
    """Read the vertex count from an ASCII or binary PLY header."""

    with path.open("rb") as stream:
        if stream.readline(256).rstrip(b"\r\n") != b"ply":
            return None
        for _ in range(4096):
            raw_line = stream.readline(4096)
            if not raw_line:
                return None
            line = raw_line.decode("ascii", errors="replace").strip()
            if line.startswith("element vertex "):
                try:
                    return int(line.rsplit(" ", 1)[1])
                except ValueError:
                    return None
            if line == "end_header":
                return None
    return None


class VramSampler:
    """Best-effort per-process NVIDIA VRAM sampler."""

    def __init__(
        self,
        pid: int,
        interval_seconds: float = 0.25,
        baseline_total_mib: float | None = None,
    ):
        self.pid = pid
        self.interval_seconds = interval_seconds
        self.peak_mib: float | None = None
        self.baseline_total_mib = baseline_total_mib
        self.peak_total_delta_mib: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def total_used_mib() -> float | None:
        if shutil.which("nvidia-smi") is None:
            return None
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=2,
            )
            values = [float(line.strip()) for line in result.stdout.splitlines() if line.strip()]
            return sum(values) if values else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def start(self) -> None:
        if shutil.which("nvidia-smi") is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> float | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self.peak_mib is not None:
            return self.peak_mib
        return self.peak_total_delta_mib

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=pid,used_gpu_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                for line in result.stdout.splitlines():
                    fields = [field.strip() for field in line.split(",")]
                    if len(fields) == 2 and fields[0] == str(self.pid):
                        used = float(fields[1])
                        self.peak_mib = used if self.peak_mib is None else max(self.peak_mib, used)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            if self.baseline_total_mib is not None:
                total_used = self.total_used_mib()
                if total_used is not None:
                    delta = max(0.0, total_used - self.baseline_total_mib)
                    self.peak_total_delta_mib = (
                        delta if self.peak_total_delta_mib is None
                        else max(self.peak_total_delta_mib, delta)
                    )
            self._stop.wait(self.interval_seconds)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _artifact_metadata(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}

def hardware_inventory() -> dict[str, Any]:
    """Capture reproducibility-relevant GPU, driver and thermal metadata."""

    inventory: dict[str, Any] = {
        "gpu": None,
        "driver_version": None,
        "cuda_version": None,
        "temperature_c": None,
        "power_limit_w": None,
    }
    if shutil.which("nvidia-smi") is None:
        return inventory
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,temperature.gpu,power.limit",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        first = next(
            (line for line in query.stdout.splitlines() if line.strip()),
            "",
        )
        fields = [field.strip() for field in first.split(",")]
        if len(fields) == 4:
            inventory["gpu"] = fields[0] or None
            inventory["driver_version"] = fields[1] or None
            for field, key in (
                (fields[2], "temperature_c"),
                (fields[3], "power_limit_w"),
            ):
                try:
                    inventory[key] = float(field)
                except ValueError:
                    inventory[key] = None
        version = subprocess.run(
            ["nvidia-smi"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        match = re.search(
            r"CUDA(?: UMD)? Version:\s*([0-9.]+)",
            version.stdout,
        )
        if match:
            inventory["cuda_version"] = match.group(1)
    except (OSError, StopIteration, subprocess.SubprocessError, ValueError):
        pass
    return inventory


def run_one(
    suite: BenchmarkSuite,
    backend: BenchmarkBackend,
    case: BenchmarkCase,
    repetition: int,
    output_root: Path,
    environment: Mapping[str, str] | None = None,
    inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data_path = Path(expand_environment(case.data_path, environment)).resolve()
    run_dir = output_root / suite.name / case.name / backend.name / f"run-{repetition:03d}"
    if run_dir.exists():
        raise FileExistsError(f"benchmark run directory already exists: {run_dir}")
    if run_dir == data_path or data_path in run_dir.parents or run_dir in data_path.parents:
        raise ValueError("benchmark outputs and source dataset must be separate trees")

    # Harness logs must remain outside the trainer output directory because the
    # native contract intentionally rejects pre-existing output artifacts.
    trainer_output_dir = run_dir / "artifacts"
    inventory_payload = dict(inventory) if inventory is not None else dataset_inventory(data_path)
    run_dir.mkdir(parents=True)
    # Create bind-mount sources as the invoking operator. Otherwise Docker
    # creates a missing host directory as root before starting a non-root
    # trainer, which makes the mounted output path unwritable.
    trainer_output_dir.mkdir()
    command = expand_command(
        backend, case, trainer_output_dir, repetition, environment
    )
    started_at = _utc_now()
    monotonic_start = time.perf_counter()
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    baseline_total_mib = VramSampler.total_used_mib()
    hardware_before = hardware_inventory()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        sampler = VramSampler(process.pid, baseline_total_mib=baseline_total_mib)
        sampler.start()
        return_code = process.wait()
        peak_vram_mib = sampler.stop()
    hardware_after = hardware_inventory()
    wall_seconds = time.perf_counter() - monotonic_start

    artifacts = sorted(run_dir.glob(backend.required_artifact_glob))
    ply_artifacts = [
        path for path in artifacts if path.is_file() and path.suffix.lower() == ".ply"
    ]
    valid_plys = [path for path in ply_artifacts if read_ply_vertex_count(path) is not None]
    status = "completed" if return_code == 0 and valid_plys else "failed"
    artifact_payload = {
        path.relative_to(run_dir).as_posix(): {
            **_artifact_metadata(path),
            "vertices": read_ply_vertex_count(path) if path.suffix.lower() == ".ply" else None,
        }
        for path in artifacts
        if path.is_file()
    }
    observation = {
        "benchmark_schema_version": SUITE_SCHEMA_VERSION,
        "suite": suite.name,
        "case": case.name,
        "backend": backend.name,
        "repetition": repetition,
        "status": status,
        "return_code": return_code,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "command": command,
        "dataset": inventory_payload,
        "parameters": {
            **case.parameters,
            "seed": int(case.parameters["seed"]) + repetition - 1,
        },
        "timings": {"wall_seconds": wall_seconds},
        "hardware": {
            "peak_vram_mib": peak_vram_mib,
            "before": hardware_before,
            "after": hardware_after,
        },
        "artifacts": artifact_payload,
        "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
    }
    _write_json_atomic(run_dir / "benchmark_run.json", observation)
    return observation


def percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of an empty sequence")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize_observations(observations: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for observation in observations:
        key = (str(observation["case"]), str(observation["backend"]))
        groups.setdefault(key, []).append(observation)
    summaries: list[dict[str, Any]] = []
    for (case, backend), group in sorted(groups.items()):
        successes = [item for item in group if item["status"] == "completed"]
        wall = [float(item["timings"]["wall_seconds"]) for item in successes]
        peaks = [
            float(item["hardware"]["peak_vram_mib"])
            for item in successes
            if item["hardware"].get("peak_vram_mib") is not None
        ]
        wall_mean = statistics.mean(wall) if wall else None
        wall_stdev = statistics.stdev(wall) if len(wall) >= 2 else None
        ci95 = (
            None
            if wall_stdev is None
            else 1.96 * wall_stdev / math.sqrt(len(wall))
        )
        summaries.append({
            "case": case,
            "backend": backend,
            "runs": len(group),
            "successful_runs": len(successes),
            "wall_seconds": None if not wall else {
                "min": min(wall),
                "mean": wall_mean,
                "median": statistics.median(wall),
                "p95": percentile_nearest_rank(wall, 0.95),
                "max": max(wall),
                "stdev": wall_stdev,
                "mean_ci95": (
                    None
                    if ci95 is None
                    else [wall_mean - ci95, wall_mean + ci95]
                ),
            },
            "peak_vram_mib": None if not peaks else {
                "median": statistics.median(peaks), "max": max(peaks)
            },
        })
    return summaries


def run_benchmark_suite(
    suite: BenchmarkSuite,
    output_root: Path,
    selected_backends: set[str] | None = None,
    selected_cases: set[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for case in suite.cases:
        if selected_cases and case.name not in selected_cases:
            continue
        data_path = Path(expand_environment(case.data_path, environment)).resolve()
        inventory = dataset_inventory(data_path)
        for backend in suite.backends:
            if selected_backends and backend.name not in selected_backends:
                continue
            for repetition in range(1, suite.repetitions + 1):
                observations.append(
                    run_one(
                        suite,
                        backend,
                        case,
                        repetition,
                        output_root,
                        environment,
                        inventory,
                    )
                )
    if not observations:
        raise ValueError("benchmark selection produced no runs")
    report = {
        "benchmark_schema_version": SUITE_SCHEMA_VERSION,
        "suite": suite.name,
        "generated_at": _utc_now(),
        "summaries": summarize_observations(observations),
        "runs": observations,
    }
    _write_json_atomic(output_root / suite.name / "benchmark_summary.json", report)
    return report
