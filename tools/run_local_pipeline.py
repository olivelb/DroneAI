"""Run the complete DroneAI pipeline locally with one resumable orchestrator."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.pipeline_params import PIPELINE_DEFAULTS
from shared.json_io import atomic_write_json as write_json
from shared.quality_profiles import quality_profile

WORKSPACE_MARKER = ".droneai-local-workspace.json"
MANIFEST_NAME = "pipeline_run.json"
STAGE_ORDER = ("colmap", "gaussian", "detection")
MODERN_DEFAULTS = PIPELINE_DEFAULTS["modern"]
FAST_DEFAULTS = quality_profile("fast-v2").parameters
NORMAL_DEFAULTS = quality_profile("normal-v3").parameters
HIGH_QUALITY_DEFAULTS = quality_profile("high-quality-v4").parameters


@dataclass(frozen=True)
class PipelineProfile:
    colmap_args: tuple[str, ...]
    gaussian_profile: str
    gaussian_backend: str
    detection_profile: str


def production_colmap_args(
    *,
    image_size: str,
    maximum_features: str,
) -> tuple[str, ...]:
    """Return the complete production-like COLMAP command envelope."""

    return (
        "--stage",
        "undistort",
        "--selection",
        "uniform",
        "--matcher",
        "gps",
        "--engine",
        str(MODERN_DEFAULTS["alignment_engine"]),
        "--feature-type",
        str(MODERN_DEFAULTS["feature_type"]),
        "--matcher-type",
        "SIFT_BRUTEFORCE",
        "--camera-model",
        str(MODERN_DEFAULTS["camera_model"]),
        "--feature-max-image-size",
        image_size,
        "--feature-max-num-features",
        maximum_features,
        "--global-ba-iterations",
        str(MODERN_DEFAULTS["global_mapper_ba_iterations"]),
        "--global-ceres-iterations",
        str(MODERN_DEFAULTS["global_mapper_ceres_iterations"]),
        *(
            ("--no-global-retriangulation",)
            if MODERN_DEFAULTS["global_mapper_skip_retriangulation"]
            else ("--global-retriangulation",)
        ),
        "--mapping-timeout-seconds",
        str(MODERN_DEFAULTS["mapping_timeout_seconds"]),
    )


PROFILES = {
    "smoke": PipelineProfile(
        colmap_args=(
            "--stage",
            "undistort",
            "--max-images",
            "25",
            "--selection",
            "contiguous",
            "--matcher",
            "sequential",
            "--feature-max-image-size",
            "2400",
        ),
        gaussian_profile="smoke",
        gaussian_backend="dronegs",
        detection_profile="smoke",
    ),
    "fast": PipelineProfile(
        colmap_args=production_colmap_args(
            image_size=str(FAST_DEFAULTS["feature_max_image_size"]),
            maximum_features=str(FAST_DEFAULTS["feature_max_num_features"]),
        ),
        gaussian_profile="fast",
        gaussian_backend="dronegs",
        detection_profile="full",
    ),
    "normal": PipelineProfile(
        colmap_args=production_colmap_args(
            image_size=str(NORMAL_DEFAULTS["feature_max_image_size"]),
            maximum_features=str(NORMAL_DEFAULTS["feature_max_num_features"]),
        ),
        gaussian_profile="normal",
        gaussian_backend="dronegs",
        detection_profile="full",
    ),
    "high-quality": PipelineProfile(
        colmap_args=production_colmap_args(
            image_size=str(HIGH_QUALITY_DEFAULTS["feature_max_image_size"]),
            maximum_features=str(HIGH_QUALITY_DEFAULTS["feature_max_num_features"]),
        ),
        gaussian_profile="high-quality",
        gaussian_backend="dronegs",
        detection_profile="full",
    ),
    "standard": PipelineProfile(
        colmap_args=production_colmap_args(
            image_size=str(MODERN_DEFAULTS["feature_max_image_size"]),
            maximum_features=str(MODERN_DEFAULTS["feature_max_num_features"]),
        ),
        gaussian_profile="low-memory",
        gaussian_backend="dronegs",
        detection_profile="full",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--from-stage", choices=STAGE_ORDER, default=STAGE_ORDER[0])
    parser.add_argument("--to-stage", choices=STAGE_ORDER, default=STAGE_ORDER[-1])
    parser.add_argument(
        "--force-stage",
        choices=STAGE_ORDER,
        action="append",
        default=[],
        help="Rebuild this stage and every dependent stage; repeat if needed.",
    )
    parser.add_argument("--keep-detection-tiles", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def selected_stages(start: str, end: str) -> tuple[str, ...]:
    start_index = STAGE_ORDER.index(start)
    end_index = STAGE_ORDER.index(end)
    if start_index > end_index:
        raise ValueError("--from-stage must not come after --to-stage")
    return STAGE_ORDER[start_index : end_index + 1]


def propagated_forces(requested: list[str]) -> set[str]:
    forced: set[str] = set()
    for stage in requested:
        forced.update(STAGE_ORDER[STAGE_ORDER.index(stage) :])
    return forced


def stage_command(
    stage: str,
    *,
    dataset: Path,
    workspace: Path,
    profile: PipelineProfile,
    forced: bool,
    keep_detection_tiles: bool,
) -> list[str]:
    if stage == "colmap":
        command = [
            str(REPO_ROOT / "tools" / "run_local_colmap.sh"),
            str(dataset),
            str(workspace),
            *profile.colmap_args,
        ]
    elif stage == "gaussian":
        command = [
            str(REPO_ROOT / "tools" / "run_local_gaussian.sh"),
            str(workspace),
            "--profile",
            profile.gaussian_profile,
            "--backend",
            profile.gaussian_backend,
        ]
    else:
        command = [
            str(REPO_ROOT / "tools" / "run_local_detection.sh"),
            str(workspace),
            "--source",
            f"orthomosaic.{profile.gaussian_profile}.tif",
            "--profile",
            profile.detection_profile,
        ]
        if keep_detection_tiles:
            command.append("--keep-tiles")
    if forced:
        command.append("--force")
    return command


def colmap_complete(workspace: Path) -> tuple[bool, str]:
    required = [workspace / "dense" / "sparse" / name for name in ("cameras.bin", "images.bin", "points3D.bin")]
    required.append(workspace / "alignment_transform.json")
    missing = [path.name for path in required if not path.is_file()]
    image_root = workspace / "dense" / "images"
    image_count = sum(1 for path in image_root.rglob("*") if path.is_file()) if image_root.is_dir() else 0
    if missing or image_count < 3:
        return False, f"missing={missing}, undistorted_images={image_count}"
    return True, f"{image_count} undistorted images and aligned sparse model"


def gaussian_complete(
    workspace: Path,
    profile: PipelineProfile,
) -> tuple[bool, str]:
    report_path = workspace / f"gaussian_run.{profile.gaussian_profile}.json"
    report = read_json(report_path)
    outputs = [
        workspace / f"orthomosaic.{profile.gaussian_profile}.tif",
        workspace / f"orthomosaic.{profile.gaussian_profile}.height.tif",
    ]
    missing = [path.name for path in outputs if not path.is_file()]
    if not report or report.get("status") != "completed" or missing:
        return False, f"report_status={report and report.get('status')}, missing={missing}"
    return True, f"completed report and {len(outputs)} raster outputs"


def detection_complete(
    workspace: Path,
    profile: PipelineProfile,
) -> tuple[bool, str]:
    output_dir = workspace / "detection_runs" / profile.detection_profile
    report = read_json(output_dir / "detection_run.json")
    outputs = [
        output_dir / "detections.json",
        output_dir / "detections.geojson",
        output_dir / "orthomosaic.annotated.tif",
    ]
    missing = [path.name for path in outputs if not path.is_file()]
    if not report or report.get("status") != "completed" or missing:
        return False, f"report_status={report and report.get('status')}, missing={missing}"
    return True, f"completed report and {len(outputs)} detection outputs"


def stage_complete(
    stage: str,
    workspace: Path,
    profile: PipelineProfile,
) -> tuple[bool, str]:
    if stage == "colmap":
        return colmap_complete(workspace)
    if stage == "gaussian":
        return gaussian_complete(workspace, profile)
    return detection_complete(workspace, profile)


def manifest_path(workspace: Path) -> Path:
    if (workspace / WORKSPACE_MARKER).is_file():
        return workspace / MANIFEST_NAME
    return workspace.parent / f".{workspace.name}.{MANIFEST_NAME}"


def persist_manifest(workspace: Path, manifest: dict[str, Any]) -> Path:
    target = manifest_path(workspace)
    write_json(target, manifest)
    final_target = workspace / MANIFEST_NAME
    if (workspace / WORKSPACE_MARKER).is_file():
        if target != final_target:
            write_json(final_target, manifest)
        sidecar = workspace.parent / f".{workspace.name}.{MANIFEST_NAME}"
        sidecar.unlink(missing_ok=True)
        return final_target
    return target


def run_stage(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("+", " ".join(command), flush=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log_file.write(line)
            log_file.flush()
        return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def stage_log_path(workspace: Path, stage: str) -> Path:
    """Keep bootstrap logs outside an unmarked workspace."""

    if (workspace / WORKSPACE_MARKER).is_file():
        return workspace / "pipeline_logs" / f"{stage}.log"
    return workspace.parent / f".{workspace.name}.{stage}.log"


def finalize_stage_log(workspace: Path, stage: str, current: Path) -> Path:
    """Move a bootstrap sidecar log inside once COLMAP marks the workspace."""

    if not (workspace / WORKSPACE_MARKER).is_file():
        return current
    final = workspace / "pipeline_logs" / f"{stage}.log"
    if current != final and current.is_file():
        final.parent.mkdir(parents=True, exist_ok=True)
        current.replace(final)
    return final


def build_manifest(
    args: argparse.Namespace,
    dataset: Path,
    workspace: Path,
    profile: PipelineProfile,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "orchestrator": "droneai-local-pipeline",
        "status": "running",
        "profile": args.profile,
        "profile_config": asdict(profile),
        "dataset": str(dataset),
        "workspace": str(workspace),
        "started_at": utc_now(),
        "stages": {},
    }


def main() -> int:
    args = parse_args()
    dataset = args.dataset.resolve()
    workspace = args.workspace.resolve()
    if not dataset.is_dir():
        raise ValueError(f"dataset directory does not exist: {dataset}")
    if dataset == workspace or dataset in workspace.parents or workspace in dataset.parents:
        raise ValueError("dataset and workspace must be separate directory trees")

    profile = PROFILES[args.profile]
    stages = selected_stages(args.from_stage, args.to_stage)
    forced = propagated_forces(args.force_stage)
    manifest = build_manifest(args, dataset, workspace, profile)
    started = time.monotonic()

    try:
        for stage in stages:
            is_complete, evidence = stage_complete(stage, workspace, profile)
            command = stage_command(
                stage,
                dataset=dataset,
                workspace=workspace,
                profile=profile,
                forced=stage in forced,
                keep_detection_tiles=args.keep_detection_tiles,
            )
            stage_record = {
                "status": "pending",
                "command": command,
                "forced": stage in forced,
                "evidence_before": evidence,
            }
            manifest["stages"][stage] = stage_record
            persist_manifest(workspace, manifest)

            if is_complete and stage not in forced:
                stage_record.update(status="skipped", reason="validated existing outputs")
                print(f"[{stage.upper()}] skipped: {evidence}", flush=True)
                persist_manifest(workspace, manifest)
                continue
            if args.dry_run:
                stage_record.update(status="planned")
                print(f"[{stage.upper()}] planned: {' '.join(command)}", flush=True)
                persist_manifest(workspace, manifest)
                continue

            stage_started = time.monotonic()
            stage_record.update(status="running", started_at=utc_now())
            persist_manifest(workspace, manifest)
            log_path = stage_log_path(workspace, stage)
            try:
                run_stage(command, log_path)
            finally:
                log_path = finalize_stage_log(workspace, stage, log_path)
            is_complete, evidence = stage_complete(stage, workspace, profile)
            if not is_complete:
                raise RuntimeError(f"{stage} command returned success but validation failed: {evidence}")
            stage_record.update(
                status="completed",
                finished_at=utc_now(),
                duration_seconds=time.monotonic() - stage_started,
                evidence_after=evidence,
                log=str(log_path),
            )
            persist_manifest(workspace, manifest)
    except BaseException as error:
        manifest.update(
            status="failed",
            finished_at=utc_now(),
            duration_seconds=time.monotonic() - started,
            error=f"{type(error).__name__}: {error}",
        )
        persist_manifest(workspace, manifest)
        raise

    manifest.update(
        status="planned" if args.dry_run else "completed",
        finished_at=utc_now(),
        duration_seconds=time.monotonic() - started,
    )
    report_path = persist_manifest(workspace, manifest)
    print(f"Pipeline manifest: {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
