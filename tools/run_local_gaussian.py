"""Train and render a local Gaussian orthophoto without service infrastructure.

This script expects a workspace prepared with ``run_local_colmap.py --stage
undistort``. It runs inside the lightweight local Gaussian image and calls the
same ``generate_gaussian_orthophoto`` function as the production worker.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
for import_path in (REPO_ROOT, APP1_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from shared.geo_alignment import (  # noqa: E402
    compute_reconstruction_alignment,
    write_alignment_transform,
)

WORKSPACE_MARKER = ".droneai-local-workspace.json"


@dataclass(frozen=True)
class GaussianProfile:
    backend: str
    iterations: int
    cap_max: int
    sh_degree: int
    data_factor: int
    max_width: int
    tile_mode: int
    resolution: float
    filter_enabled: bool
    seed: int
    optimizer_profile: str
    pruning_policy: str
    raster_profile: str
    sh_degree_interval: int
    topology_cooldown: int
    photometric_finish: int
    photometric_mse_percent: int


PROFILES: dict[str, GaussianProfile] = {
    # Fast integration check. The result demonstrates the complete path but is
    # not intended for visual assessment.
    "smoke": GaussianProfile(
        backend="dronegs",
        iterations=500,
        cap_max=100_000,
        sh_degree=0,
        data_factor=8,
        max_width=1024,
        tile_mode=4,
        resolution=0.25,
        filter_enabled=False,
        seed=42,
        optimizer_profile="dev38-staged-rotation008-absgrad050-fastgs",
        pruning_policy="lichtfeld-bounds",
        raster_profile="fastgs",
        sh_degree_interval=1_000,
        topology_cooldown=100,
        photometric_finish=100,
        photometric_mse_percent=100,
    ),
    # Conservative default for the RTX 4070 Laptop GPU used for validation.
    "low-memory": GaussianProfile(
        backend="dronegs",
        iterations=5_000,
        cap_max=500_000,
        sh_degree=1,
        data_factor=4,
        max_width=1600,
        tile_mode=4,
        resolution=0.10,
        filter_enabled=True,
        seed=42,
        optimizer_profile="dev38-staged-rotation008-absgrad050-fastgs",
        pruning_policy="lichtfeld-bounds",
        raster_profile="fastgs",
        sh_degree_interval=1_000,
        topology_cooldown=1_000,
        photometric_finish=1_000,
        photometric_mse_percent=100,
    ),
    # A follow-up profile for better quality once the low-memory run is stable.
    "balanced": GaussianProfile(
        backend="dronegs",
        iterations=15_000,
        cap_max=1_500_000,
        sh_degree=3,
        data_factor=4,
        max_width=1600,
        tile_mode=4,
        resolution=0.05,
        filter_enabled=True,
        seed=42,
        optimizer_profile="dev38-staged-rotation008-absgrad050-fastgs",
        pruning_policy="lichtfeld-bounds",
        raster_profile="fastgs",
        sh_degree_interval=1_000,
        topology_cooldown=1_000,
        photometric_finish=1_000,
        photometric_mse_percent=100,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="low-memory")
    parser.add_argument("--backend", choices=("dronegs", "lichtfeld"))
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--cap-max", type=int)
    parser.add_argument("--sh-degree", type=int, choices=range(4))
    parser.add_argument("--data-factor", type=int, choices=(1, 2, 4, 8))
    parser.add_argument("--max-width", type=int)
    parser.add_argument("--tile-mode", type=int, choices=(1, 2, 4))
    parser.add_argument("--resolution", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--optimizer-profile")
    parser.add_argument(
        "--pruning-policy",
        choices=("original", "lichtfeld-bounds"),
    )
    parser.add_argument(
        "--raster-profile",
        choices=("auto", "bounded", "fastgs"),
    )
    parser.add_argument("--sh-degree-interval", type=int)
    parser.add_argument("--topology-cooldown", type=int)
    parser.add_argument("--photometric-finish", type=int)
    parser.add_argument("--photometric-mse-percent", type=int)
    parser.add_argument(
        "--filter",
        dest="filter_enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def resolve_profile(args: argparse.Namespace) -> GaussianProfile:
    profile = PROFILES[args.profile]
    overrides = {
        field: getattr(args, field)
        for field in asdict(profile)
        if getattr(args, field, None) is not None
    }
    resolved = replace(profile, **overrides)
    if resolved.iterations <= 0:
        raise ValueError("iterations must be positive")
    if resolved.cap_max <= 0:
        raise ValueError("cap-max must be positive")
    if not 1 <= resolved.max_width <= 4096:
        raise ValueError("max-width must be between 1 and 4096")
    if resolved.resolution <= 0:
        raise ValueError("resolution must be positive")
    if resolved.seed < 0:
        raise ValueError("seed must be non-negative")
    for field_name in (
        "sh_degree_interval",
        "topology_cooldown",
        "photometric_finish",
    ):
        if getattr(resolved, field_name) < 0:
            raise ValueError(f"{field_name.replace('_', '-')} must be non-negative")
    if resolved.sh_degree_interval == 0:
        raise ValueError("sh-degree-interval must be positive")
    if not 0 <= resolved.photometric_mse_percent <= 100:
        raise ValueError("photometric-mse-percent must be between 0 and 100")
    return resolved


def validate_workspace(workspace: Path) -> tuple[Path, Path, str]:
    workspace = workspace.resolve()
    if not (workspace / WORKSPACE_MARKER).is_file():
        raise ValueError("workspace has no DroneAI local marker")

    dense_path = workspace / "dense"
    sparse_path = workspace / "sparse"
    aligned_path = workspace / "sparse_geo"
    required_dense_files = ("cameras.bin", "images.bin", "points3D.bin")
    missing = [
        str(dense_path / "sparse" / filename)
        for filename in required_dense_files
        if not (dense_path / "sparse" / filename).is_file()
    ]
    if missing:
        raise ValueError(
            "workspace is not undistorted; missing " + ", ".join(missing)
        )
    if not sparse_path.is_dir() or not aligned_path.is_dir():
        raise ValueError("workspace needs both sparse and sparse_geo models")
    image_count = sum(1 for path in (dense_path / "images").rglob("*") if path.is_file())
    if image_count < 3:
        raise ValueError("workspace needs at least three undistorted images")

    crs_path = workspace / "geo_data.txt.crs"
    if not crs_path.is_file() or not crs_path.read_text(encoding="utf-8").strip():
        raise ValueError("workspace has no projected CRS in geo_data.txt.crs")
    return dense_path, aligned_path, crs_path.read_text(encoding="utf-8").strip()


def ensure_transform(workspace: Path, aligned_path: Path) -> Path:
    transform_path = workspace / "alignment_transform.json"
    if transform_path.is_file():
        return transform_path
    sparse_candidates = sorted(
        path
        for path in (workspace / "sparse").iterdir()
        if path.is_dir() and (path / "cameras.bin").is_file()
    )
    if not sparse_candidates:
        raise ValueError("workspace has no source sparse model")
    transform = compute_reconstruction_alignment(sparse_candidates[0], aligned_path)
    return write_alignment_transform(transform_path, transform)


def output_paths(
    workspace: Path,
    profile_name: str,
    requested_output: Path | None,
) -> tuple[Path, Path, Path]:
    ortho_path = (
        requested_output.resolve()
        if requested_output
        else workspace / f"orthomosaic.{profile_name}.tif"
    )
    try:
        ortho_path.relative_to(workspace)
    except ValueError as error:
        raise ValueError("output must stay inside the marked workspace") from error
    height_path = ortho_path.with_suffix(".height.tif")
    checkpoint_path = workspace / "gaussian_checkpoints" / profile_name
    return ortho_path, height_path, checkpoint_path


def clear_generated_outputs(
    ortho_path: Path,
    height_path: Path,
    checkpoint_path: Path,
) -> None:
    for path in (ortho_path, height_path):
        if path.exists():
            path.unlink()
    if checkpoint_path.exists():
        shutil.rmtree(checkpoint_path)


def write_run_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    profile = resolve_profile(args)
    workspace = args.workspace.resolve()
    dense_path, aligned_path, projected_crs = validate_workspace(workspace)
    transform_path = ensure_transform(workspace, aligned_path)
    ortho_path, height_path, checkpoint_path = output_paths(
        workspace,
        args.profile,
        args.output,
    )
    if any(path.exists() for path in (ortho_path, height_path, checkpoint_path)):
        if not args.force:
            raise ValueError(
                "generated outputs already exist; pass --force to replace only "
                f"the {args.profile!r} profile artifacts"
            )
        clear_generated_outputs(ortho_path, height_path, checkpoint_path)

    from gaussian_ortho.generate_gaussian_orthophoto import (
        generate_gaussian_orthophoto,
    )
    from gaussian_training import resolve_training_backend

    backend = resolve_training_backend(profile.backend)
    if not backend.is_available():
        raise RuntimeError(
            f"{profile.backend} trainer is missing from the local Gaussian image"
        )

    report_path = workspace / f"gaussian_run.{args.profile}.json"
    started_at = time.time()
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "profile": args.profile,
        "parameters": asdict(profile),
        "workspace": str(workspace),
        "trainer_backend": profile.backend,
        "started_at": started_at,
    }
    write_run_report(report_path, report)
    try:
        result = generate_gaussian_orthophoto(
            dense_path=str(dense_path),
            ortho_file=str(ortho_path),
            utm_crs=projected_crs,
            vol_id=f"local-{args.profile}",
            transform_file=str(transform_path),
            resolution=profile.resolution,
            iterations=profile.iterations,
            sh_degree=profile.sh_degree,
            data_factor=profile.data_factor,
            max_width=profile.max_width,
            tile_mode=profile.tile_mode,
            cap_max=profile.cap_max,
            filter_enabled=profile.filter_enabled,
            checkpoint_dir=str(checkpoint_path),
            verbose=args.verbose,
            trainer_backend=profile.backend,
            training_seed=profile.seed,
            dronegs_optimizer_profile=profile.optimizer_profile,
            dronegs_pruning_policy=profile.pruning_policy,
            dronegs_raster_profile=profile.raster_profile,
            dronegs_sh_degree_interval=profile.sh_degree_interval,
            dronegs_topology_cooldown=profile.topology_cooldown,
            dronegs_photometric_finish=profile.photometric_finish,
            dronegs_photometric_mse_percent=profile.photometric_mse_percent,
        )
    except Exception as error:
        report.update(
            status="failed",
            finished_at=time.time(),
            duration_seconds=time.time() - started_at,
            error=f"{type(error).__name__}: {error}",
        )
        write_run_report(report_path, report)
        raise

    report.update(
        status="completed",
        finished_at=time.time(),
        duration_seconds=time.time() - started_at,
        result=result,
    )
    write_run_report(report_path, report)
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
