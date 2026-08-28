"""Stable backend boundary for Gaussian training implementations."""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
)

from .dataset_identity import compute_dataset_identity
from .manifest_contract import (
    manifest_parameter_matches,
    promote_run_manifest,
    sha256_file,
    validate_run_manifest,
)

ProgressCallback = Callable[[int, float, int], None]
CancellationCheck = Callable[[], None]
CheckpointCallback = Callable[[Path, int], None]
SUPPORTED_STRATEGIES = {"mrnf"}
SUPPORTED_DRONEGS_PRUNING_POLICIES = {"spatial-bounds"}
SUPPORTED_DRONEGS_RASTER_PROFILES = {"fastgs"}
SUPPORTED_DRONEGS_INITIAL_SCALE_POLICIES = {"local-knn", "projected-knn"}
SUPPORTED_DRONEGS_TEST_SPLITS = {"modulo", "spatial-block"}
SUPPORTED_DRONEGS_BACKGROUND_MODES = {"black", "random"}
SUPPORTED_DRONEGS_LOSS_PIXEL_MASKS = {"active", "all"}


def _require_supported(name: str, value: str, supported: set[str]) -> None:
    if value not in supported:
        raise ValueError(f"unsupported DroneGS {name}")


@dataclass(frozen=True)
class DroneGSTuning:
    """Optional native controls layered on top of trainer contract v1."""

    profile_id: str = DRONEGS_PRODUCTION_PROFILE_V1.profile_id
    qualification_policy_id: str = DRONEGS_QUALIFICATION_POLICY_ID
    optimizer_profile: str = DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile
    pruning_policy: str = DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy
    raster_profile: str = DRONEGS_PRODUCTION_PROFILE_V1.raster_profile
    background_mode: str = "black"
    loss_pixel_mask: str = "active"
    opacity_sh_enabled: bool = False
    sh_degree_interval: int = DRONEGS_PRODUCTION_PROFILE_V1.sh_degree_interval
    topology_cooldown: int = DRONEGS_PRODUCTION_PROFILE_V1.topology_cooldown
    photometric_finish: int = DRONEGS_PRODUCTION_PROFILE_V1.photometric_finish
    photometric_mse_percent: int = DRONEGS_PRODUCTION_PROFILE_V1.photometric_mse_percent
    adaptive_growth_target: bool = False
    adaptive_native_crop_tiles: bool = False
    initial_scale_policy: str = "local-knn"
    initial_max_projected_sigma_pixels: float = 2.0
    maximum_scale_growth_factor: float = 54.59815
    prefetch_depth: int = 1
    decode_workers: int = 1
    host_image_cache_mib: int = 2_048
    jpeg_idct_scale: int = 0
    test_every: int = DRONEGS_PRODUCTION_PROFILE_V1.test_every
    test_split: str = DRONEGS_PRODUCTION_PROFILE_V1.test_split
    test_guard_percent: int = DRONEGS_PRODUCTION_PROFILE_V1.test_guard_percent
    save_eval_images: bool = True
    checkpoint_every: int = DRONEGS_PRODUCTION_PROFILE_V1.checkpoint_every
    resume_from: str | None = None
    canary_min_psnr: float | None = DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr
    canary_min_ssim: float | None = DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id must not be empty")
        if not isinstance(self.qualification_policy_id, str) or not self.qualification_policy_id.strip():
            raise ValueError("qualification_policy_id must not be empty")
        if self.optimizer_profile != "reference-absolute":
            raise ValueError("unsupported DroneGS optimizer_profile")
        if not isinstance(self.adaptive_growth_target, bool):
            raise ValueError("adaptive_growth_target must be boolean")
        if not isinstance(self.adaptive_native_crop_tiles, bool):
            raise ValueError("adaptive_native_crop_tiles must be boolean")
        if not isinstance(self.opacity_sh_enabled, bool):
            raise ValueError("opacity_sh_enabled must be boolean")
        _require_supported("pruning_policy", self.pruning_policy, SUPPORTED_DRONEGS_PRUNING_POLICIES)
        _require_supported("raster_profile", self.raster_profile, SUPPORTED_DRONEGS_RASTER_PROFILES)
        _require_supported("background_mode", self.background_mode, SUPPORTED_DRONEGS_BACKGROUND_MODES)
        _require_supported("loss_pixel_mask", self.loss_pixel_mask, SUPPORTED_DRONEGS_LOSS_PIXEL_MASKS)
        _require_supported(
            "initial_scale_policy",
            self.initial_scale_policy,
            SUPPORTED_DRONEGS_INITIAL_SCALE_POLICIES,
        )
        if (
            not isinstance(self.initial_max_projected_sigma_pixels, (int, float))
            or isinstance(self.initial_max_projected_sigma_pixels, bool)
            or not 0 < self.initial_max_projected_sigma_pixels <= 64
        ):
            raise ValueError("initial_max_projected_sigma_pixels must be in (0, 64]")
        if (
            not isinstance(self.maximum_scale_growth_factor, (int, float))
            or isinstance(self.maximum_scale_growth_factor, bool)
            or not 1 <= self.maximum_scale_growth_factor <= 1024
        ):
            raise ValueError("maximum_scale_growth_factor must be in [1, 1024]")
        _require_supported("test_split", self.test_split, SUPPORTED_DRONEGS_TEST_SPLITS)
        integer_ranges = {
            "sh_degree_interval": (self.sh_degree_interval, 1, None),
            "topology_cooldown": (self.topology_cooldown, 0, None),
            "photometric_finish": (self.photometric_finish, 0, None),
            "photometric_mse_percent": (
                self.photometric_mse_percent,
                0,
                100,
            ),
            "prefetch_depth": (self.prefetch_depth, 1, 64),
            "decode_workers": (self.decode_workers, 1, 16),
            "host_image_cache_mib": (
                self.host_image_cache_mib,
                256,
                65_536,
            ),
            "jpeg_idct_scale": (self.jpeg_idct_scale, 0, 1),
            "test_every": (self.test_every, 0, None),
            "test_guard_percent": (self.test_guard_percent, 0, 100),
            "checkpoint_every": (self.checkpoint_every, 0, None),
        }
        for name, (value, minimum, maximum) in integer_ranges.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
                or (maximum is not None and value > maximum)
            ):
                raise ValueError(f"{name} is invalid")
        if self.decode_workers > self.prefetch_depth:
            raise ValueError("decode_workers must not exceed prefetch_depth")
        if self.test_every == 1:
            raise ValueError("test_every must be zero or at least two")
        if self.test_split == "modulo" and self.test_guard_percent:
            raise ValueError("test_guard_percent requires test_split='spatial-block'")
        if self.test_guard_percent and self.test_every == 0:
            raise ValueError("test_guard_percent requires test_every")
        if self.save_eval_images and self.test_every == 0:
            raise ValueError("save_eval_images requires test_every")
        if self.resume_from is not None and not self.resume_from.strip():
            raise ValueError("resume_from must not be empty")
        for name, value in {
            "canary_min_psnr": self.canary_min_psnr,
            "canary_min_ssim": self.canary_min_ssim,
        }.items():
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"{name} must be numeric")
        if (self.canary_min_psnr is not None or self.canary_min_ssim is not None) and self.test_every == 0:
            raise ValueError("canary thresholds require test_every")
        if (self.photometric_finish == 0) != (self.photometric_mse_percent == 0):
            raise ValueError("photometric_finish and photometric_mse_percent must both be zero or both be positive")


@dataclass(frozen=True)
class TrainingRequest:
    """Backend-neutral training inputs defined by CLI contract v1."""

    data_path: str
    output_path: str
    iterations: int = DRONEGS_PRODUCTION_PROFILE_V1.iterations
    strategy: str = "mrnf"
    sh_degree: int = DRONEGS_PRODUCTION_PROFILE_V1.sh_degree
    max_cap: int = DRONEGS_PRODUCTION_PROFILE_V1.cap_max
    resize_factor: int = DRONEGS_PRODUCTION_PROFILE_V1.data_factor
    max_width: int = DRONEGS_PRODUCTION_PROFILE_V1.max_width
    tile_mode: int = DRONEGS_PRODUCTION_PROFILE_V1.tile_mode
    seed: int = DRONEGS_PRODUCTION_PROFILE_V1.seed
    dataset_fingerprint: str | None = None
    dronegs: DroneGSTuning = field(default_factory=DroneGSTuning)
    tile_mode_auto: bool = False

    def __post_init__(self) -> None:
        def require_integer(
            name: str,
            value: int,
            minimum: int,
            allowed: set[int] | None = None,
        ) -> None:
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
                or (allowed is not None and value not in allowed)
            ):
                raise ValueError(f"{name} is invalid")

        if not self.data_path:
            raise ValueError("data_path must not be empty")
        if not self.output_path:
            raise ValueError("output_path must not be empty")
        require_integer("iterations", self.iterations, 1)
        if not isinstance(self.strategy, str) or self.strategy not in SUPPORTED_STRATEGIES:
            raise ValueError("strategy must be mrnf")
        require_integer("sh_degree", self.sh_degree, 0, {0, 1, 2, 3})
        require_integer("max_cap", self.max_cap, 1)
        require_integer("resize_factor", self.resize_factor, 1, {1, 2, 4, 8})
        require_integer("max_width", self.max_width, 1)
        if self.max_width > 4096:
            raise ValueError("max_width must be between 1 and 4096")
        require_integer("tile_mode", self.tile_mode, 1, {1, 2, 4})
        require_integer("seed", self.seed, 0)
        if not isinstance(self.dronegs, DroneGSTuning):
            raise ValueError("dronegs must be a DroneGSTuning instance")
        if not isinstance(self.tile_mode_auto, bool):
            raise ValueError("tile_mode_auto must be a boolean")
        if self.dataset_fingerprint is not None and not (
            isinstance(self.dataset_fingerprint, str) and self.dataset_fingerprint.strip()
        ):
            raise ValueError("dataset_fingerprint must not be empty")
        if self.dronegs.topology_cooldown > self.iterations:
            raise ValueError("topology_cooldown must not exceed iterations")
        if self.dronegs.photometric_finish > self.iterations:
            raise ValueError("photometric_finish must not exceed iterations")
        if self.dronegs.profile_id == DRONEGS_PRODUCTION_PROFILE_V1.profile_id:
            production = DRONEGS_PRODUCTION_PROFILE_V1
            effective = {
                "iterations": self.iterations,
                "sh_degree": self.sh_degree,
                "max_cap": self.max_cap,
                "resize_factor": self.resize_factor,
                "max_width": self.max_width,
                "tile_mode": self.tile_mode,
                "seed": self.seed,
                "optimizer_profile": self.dronegs.optimizer_profile,
                "pruning_policy": self.dronegs.pruning_policy,
                "raster_profile": self.dronegs.raster_profile,
                "sh_degree_interval": self.dronegs.sh_degree_interval,
                "topology_cooldown": self.dronegs.topology_cooldown,
                "photometric_finish": self.dronegs.photometric_finish,
                "photometric_mse_percent": (self.dronegs.photometric_mse_percent),
                "test_every": self.dronegs.test_every,
                "test_split": self.dronegs.test_split,
                "test_guard_percent": self.dronegs.test_guard_percent,
            }
            expected = {
                "iterations": production.iterations,
                "sh_degree": production.sh_degree,
                "max_cap": production.cap_max,
                "resize_factor": production.data_factor,
                "max_width": production.max_width,
                "tile_mode": production.tile_mode,
                "seed": production.seed,
                "optimizer_profile": production.optimizer_profile,
                "pruning_policy": production.pruning_policy,
                "raster_profile": production.raster_profile,
                "sh_degree_interval": production.sh_degree_interval,
                "topology_cooldown": production.topology_cooldown,
                "photometric_finish": production.photometric_finish,
                "photometric_mse_percent": (production.photometric_mse_percent),
                "test_every": production.test_every,
                "test_split": production.test_split,
                "test_guard_percent": production.test_guard_percent,
            }
            mismatches = [
                name
                for name, value in effective.items()
                if value != expected[name] and not (name == "tile_mode" and self.tile_mode_auto)
            ]
            if mismatches:
                raise ValueError(
                    f"{production.profile_id} cannot be combined with "
                    "overrides; set profile_id='custom' for: " + ", ".join(mismatches)
                )


@dataclass(frozen=True)
class TrainingResult:
    """Normalized result returned to the existing orthophoto pipeline."""

    backend: str
    ply_path: Path
    manifest_path: Path | None = None
    effective_seed: int | None = None


def evaluate_quality_canary(
    manifest: Mapping[str, Any],
    tuning: DroneGSTuning,
) -> dict[str, Any]:
    """Evaluate post-training metrics without changing training compatibility."""

    metrics = manifest.get("metrics", {})
    failures = []
    if tuning.canary_min_psnr is not None and (metrics.get("psnr") is None or metrics["psnr"] < tuning.canary_min_psnr):
        failures.append("psnr")
    if tuning.canary_min_ssim is not None and (metrics.get("ssim") is None or metrics["ssim"] < tuning.canary_min_ssim):
        failures.append("ssim")
    return {
        "contract_version": 1,
        "backend": "dronegs",
        "qualification_policy_id": tuning.qualification_policy_id,
        "psnr": metrics.get("psnr"),
        "ssim": metrics.get("ssim"),
        "minimum_psnr": tuning.canary_min_psnr,
        "minimum_ssim": tuning.canary_min_ssim,
        "test_split": tuning.test_split,
        "test_guard_percent": tuning.test_guard_percent,
        "training_image_count": manifest.get("dataset", {}).get("training_image_count"),
        "held_out_image_count": manifest.get("dataset", {}).get("held_out_image_count"),
        "ignored_image_count": manifest.get("dataset", {}).get("ignored_image_count", 0),
        "status": "passed" if not failures else "failed",
        "failed_metrics": failures,
    }


def write_quality_canary(
    output_path: str | Path,
    canary: Mapping[str, Any],
) -> Path:
    """Persist a quality-gate decision independently of trainer artifacts."""

    canary_path = Path(output_path) / "canary_result.json"
    temporary_canary = canary_path.with_suffix(".json.tmp")
    temporary_canary.write_text(
        json.dumps(dict(canary), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_canary.replace(canary_path)
    return canary_path


class TrainingBackend(Protocol):
    """Trainer implementation boundary used by DroneAI."""

    name: str

    def is_available(self) -> bool: ...

    def binary_sha256(self) -> str: ...

    def train(
        self,
        request: TrainingRequest,
        report_fn: ProgressCallback | None = None,
        verbose: bool = False,
        cancellation_check: CancellationCheck | None = None,
        checkpoint_fn: CheckpointCallback | None = None,
    ) -> TrainingResult: ...


class DroneGSBackend:
    """Adapter for the native DroneGS contract-v1 executable."""

    name = "dronegs"

    def __init__(
        self,
        binary: str | None = None,
        environment: Mapping[str, str] | None = None,
    ):
        self._binary = binary
        self._environment = os.environ if environment is None else environment

    def _resolved_binary(self) -> str | None:
        if self._binary:
            return self._binary
        configured = self._environment.get("DRONEGS_BIN")
        if configured:
            return configured
        app_root = Path(__file__).resolve().parents[1]
        candidate = app_root / "dronegs" / "build" / "dronegs"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return shutil.which("dronegs")

    def is_available(self) -> bool:
        binary = self._resolved_binary()
        if not binary:
            return False
        if os.path.sep in binary:
            return os.path.isfile(binary) and os.access(binary, os.X_OK)
        return shutil.which(binary) is not None

    def binary_sha256(self) -> str:
        binary = self._resolved_binary()
        if not binary:
            raise FileNotFoundError("DroneGS binary not found; set DRONEGS_BIN")
        resolved = Path(binary if os.path.sep in binary else shutil.which(binary) or "")
        return sha256_file(resolved)

    def build_command(
        self,
        request: TrainingRequest,
        *,
        dataset_fingerprint: str | None = None,
    ) -> list[str]:
        binary = self._resolved_binary()
        if not binary:
            raise FileNotFoundError("DroneGS binary not found; set DRONEGS_BIN")
        manifest = Path(request.output_path) / "trainer_run.json"
        command = [
            binary,
            "--data-path",
            request.data_path,
            "--output-path",
            request.output_path,
            "--iter",
            str(request.iterations),
            "--strategy",
            request.strategy,
            "--sh-degree",
            str(request.sh_degree),
            "--max-cap",
            str(request.max_cap),
            "--resize-factor",
            str(request.resize_factor),
            "--max-width",
            str(request.max_width),
            "--tile-mode",
            str(request.tile_mode),
            "--adaptive-native-crop-tiles",
            "1" if request.dronegs.adaptive_native_crop_tiles else "0",
            "--seed",
            str(request.seed),
            "--run-manifest",
            str(manifest),
        ]
        tuning = request.dronegs
        command.extend(
            [
                "--profile-id",
                tuning.profile_id,
                "--optimizer-profile",
                tuning.optimizer_profile,
                "--pruning-policy",
                tuning.pruning_policy,
                "--raster-profile",
                tuning.raster_profile,
                "--background-mode",
                tuning.background_mode,
                "--loss-pixel-mask",
                tuning.loss_pixel_mask,
                "--opacity-sh",
                "1" if tuning.opacity_sh_enabled else "0",
                "--sh-degree-interval",
                str(tuning.sh_degree_interval),
                "--topology-cooldown",
                str(tuning.topology_cooldown),
                "--photometric-finish",
                str(tuning.photometric_finish),
                "--photometric-mse-percent",
                str(tuning.photometric_mse_percent),
                "--adaptive-growth-target",
                "1" if tuning.adaptive_growth_target else "0",
                "--initial-scale-policy",
                tuning.initial_scale_policy,
                "--initial-max-projected-sigma-pixels",
                str(tuning.initial_max_projected_sigma_pixels),
                "--maximum-scale-growth-factor",
                str(tuning.maximum_scale_growth_factor),
                "--prefetch-depth",
                str(tuning.prefetch_depth),
                "--decode-workers",
                str(tuning.decode_workers),
                "--host-image-cache-mib",
                str(tuning.host_image_cache_mib),
                "--jpeg-idct-scale",
                str(tuning.jpeg_idct_scale),
                "--test-every",
                str(tuning.test_every),
                "--test-split",
                tuning.test_split,
                "--test-guard-percent",
                str(tuning.test_guard_percent),
                "--save-eval-images",
                "1" if tuning.save_eval_images else "0",
            ]
        )
        fingerprint = dataset_fingerprint or request.dataset_fingerprint
        if fingerprint:
            command.extend(["--dataset-fingerprint", fingerprint])
        if tuning.checkpoint_every:
            command.extend(
                [
                    "--checkpoint-every",
                    str(tuning.checkpoint_every),
                    "--checkpoint-path",
                    str(Path(request.output_path) / "training.ckpt"),
                ]
            )
        if tuning.resume_from:
            command.extend(["--resume-from", tuning.resume_from])
        return command

    def train(
        self,
        request: TrainingRequest,
        report_fn: ProgressCallback | None = None,
        verbose: bool = False,
        cancellation_check: CancellationCheck | None = None,
        checkpoint_fn: CheckpointCallback | None = None,
    ) -> TrainingResult:
        if not self.is_available():
            raise FileNotFoundError("DroneGS binary not executable; set DRONEGS_BIN")
        data_path = Path(request.data_path).resolve()
        if not data_path.is_dir():
            raise FileNotFoundError(f"Data path does not exist: {data_path}")
        output_path = Path(request.output_path).resolve()
        if output_path == data_path or data_path in output_path.parents or output_path in data_path.parents:
            raise ValueError("DroneGS output and source dataset must be separate trees")
        if output_path.exists() and any(output_path.iterdir()) and request.dronegs.resume_from is None:
            raise FileExistsError(f"DroneGS output directory is not empty: {output_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        fingerprint = request.dataset_fingerprint or compute_dataset_identity(data_path).fingerprint
        binary_sha256 = self.binary_sha256()
        command = self.build_command(
            request,
            dataset_fingerprint=fingerprint,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(self._environment),
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            try:
                for raw_line in process.stdout:
                    output_queue.put(raw_line)
            finally:
                output_queue.put(None)

        reader = threading.Thread(
            target=read_output,
            name="dronegs-output",
            daemon=True,
        )
        reader.start()
        output_finished = False
        try:
            while process.poll() is None or not output_finished or not output_queue.empty():
                if cancellation_check is not None:
                    cancellation_check()
                try:
                    raw_line = output_queue.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    continue
                if raw_line is None:
                    output_finished = True
                    continue
                line = raw_line.rstrip()
                if verbose:
                    print(f"[DroneGS] {line}")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_name = event.get("event")
                if event_name == "progress" and report_fn:
                    report_fn(
                        int(event.get("iteration", 0)),
                        float(event.get("loss", 0.0)),
                        int(event.get("gaussians", 0)),
                    )
                elif event_name == "checkpoint_saved" and checkpoint_fn:
                    checkpoint = Path(str(event.get("path") or event.get("checkpoint") or ""))
                    if checkpoint.is_file():
                        checkpoint_fn(
                            checkpoint,
                            int(event.get("iteration", 0)),
                        )
        except BaseException:
            self._terminate_process(process)
            raise
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"DroneGS training failed with exit code {return_code}")

        manifest_path = output_path / "trainer_run.json"
        if not manifest_path.is_file():
            raise RuntimeError("DroneGS completed without trainer_run.json")
        ply_path = output_path / "point_cloud.ply"
        if not ply_path.is_file():
            raise RuntimeError("DroneGS completed without point_cloud.ply")
        try:
            manifest = promote_run_manifest(
                manifest_path,
                ply_path=ply_path,
                trainer_binary_sha256=binary_sha256,
            )
            validate_run_manifest(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"DroneGS returned an invalid run manifest: {error}") from error
        parameters = manifest["parameters"]
        if (
            manifest["dataset"]["fingerprint"] != fingerprint
            or parameters["profile_id"] != request.dronegs.profile_id
            or parameters["optimizer_profile"] != request.dronegs.optimizer_profile
            or parameters["pruning_policy"] != request.dronegs.pruning_policy
            or parameters["raster_profile"] != request.dronegs.raster_profile
            or (parameters.get("opacity_sh_enabled") is not request.dronegs.opacity_sh_enabled)
            or parameters.get("initial_scale_policy") != request.dronegs.initial_scale_policy
            or not manifest_parameter_matches(
                parameters.get("initial_max_projected_sigma_pixels"),
                request.dronegs.initial_max_projected_sigma_pixels,
            )
            or not manifest_parameter_matches(
                parameters.get("maximum_scale_growth_factor"),
                request.dronegs.maximum_scale_growth_factor,
            )
            or parameters.get("adaptive_native_crop_tiles") != int(request.dronegs.adaptive_native_crop_tiles)
            or parameters.get("test_split") != request.dronegs.test_split
            or parameters.get("test_guard_percent") != request.dronegs.test_guard_percent
            or parameters["effective_raster_profile"] != request.dronegs.raster_profile
        ):
            raise RuntimeError("DroneGS manifest does not describe the requested dataset/profile")
        canary = evaluate_quality_canary(manifest, request.dronegs)
        write_quality_canary(output_path, canary)
        if canary["failed_metrics"]:
            raise RuntimeError("DroneGS quality canary failed: " + ", ".join(canary["failed_metrics"]))
        # A completed PLY + manifest + passed canary is the durable recovery
        # point. The much larger optimizer checkpoint is only useful while
        # training is incomplete, so reclaim it after successful promotion.
        (output_path / "training.ckpt").unlink(missing_ok=True)
        return TrainingResult(
            self.name,
            ply_path,
            manifest_path=manifest_path,
            effective_seed=request.seed,
        )

    @staticmethod
    def _terminate_process(
        process: subprocess.Popen[str],
        grace_seconds: float = 10.0,
    ) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=grace_seconds)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)


def resolve_training_backend(
    name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> TrainingBackend:
    """Resolve an explicit backend or DRONEAI_GAUSSIAN_BACKEND."""

    environment = os.environ if environment is None else environment
    selected_value = name or environment.get("DRONEAI_GAUSSIAN_BACKEND", "dronegs")
    if not isinstance(selected_value, str):
        raise ValueError("Gaussian training backend name must be a string")
    selected = selected_value.lower()
    if selected == "dronegs":
        return DroneGSBackend(environment=environment)
    raise ValueError(f"unknown Gaussian training backend {selected!r}; expected dronegs")
