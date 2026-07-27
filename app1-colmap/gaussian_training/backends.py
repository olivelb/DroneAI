"""Stable backend boundary for Gaussian training implementations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol


ProgressCallback = Callable[[int, float, int], None]
SUPPORTED_STRATEGIES = {"mrnf", "mcmc", "igs+"}
SUPPORTED_DRONEGS_PRUNING_POLICIES = {"original", "lichtfeld-bounds"}
SUPPORTED_DRONEGS_RASTER_PROFILES = {"auto", "bounded", "fastgs"}


@dataclass(frozen=True)
class DroneGSTuning:
    """Optional native controls layered on top of trainer contract v1."""

    optimizer_profile: str = "dronegs-dev16"
    pruning_policy: str = "original"
    raster_profile: str = "auto"
    sh_degree_interval: int = 1_000
    topology_cooldown: int = 0
    photometric_finish: int = 0
    photometric_mse_percent: int = 0
    prefetch_depth: int = 1
    decode_workers: int = 1
    jpeg_idct_scale: int = 0
    test_every: int = 0
    save_eval_images: bool = False

    def __post_init__(self) -> None:
        # The native trainer owns the versioned optimizer-profile registry.
        # Keeping this boundary open preserves old ablation manifests while
        # the mission schema exposes only supported production choices.
        if (
            not isinstance(self.optimizer_profile, str)
            or not self.optimizer_profile.strip()
        ):
            raise ValueError("optimizer_profile must not be empty")
        if self.pruning_policy not in SUPPORTED_DRONEGS_PRUNING_POLICIES:
            raise ValueError("unsupported DroneGS pruning_policy")
        if self.raster_profile not in SUPPORTED_DRONEGS_RASTER_PROFILES:
            raise ValueError("unsupported DroneGS raster_profile")
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
            "jpeg_idct_scale": (self.jpeg_idct_scale, 0, 1),
            "test_every": (self.test_every, 0, None),
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
        if self.save_eval_images and self.test_every == 0:
            raise ValueError("save_eval_images requires test_every")
        if (self.photometric_finish == 0) != (
            self.photometric_mse_percent == 0
        ):
            raise ValueError(
                "photometric_finish and photometric_mse_percent must both "
                "be zero or both be positive"
            )


@dataclass(frozen=True)
class TrainingRequest:
    """Backend-neutral training inputs defined by CLI contract v1."""

    data_path: str
    output_path: str
    iterations: int = 30_000
    strategy: str = "mrnf"
    sh_degree: int = 3
    max_cap: int = 5_000_000
    resize_factor: int = 1
    max_width: int = 3840
    tile_mode: int = 1
    seed: int = 0
    dronegs: DroneGSTuning = field(default_factory=DroneGSTuning)

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
            raise ValueError("strategy must be mrnf, mcmc, or igs+")
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
        if self.dronegs.topology_cooldown > self.iterations:
            raise ValueError("topology_cooldown must not exceed iterations")
        if self.dronegs.photometric_finish > self.iterations:
            raise ValueError("photometric_finish must not exceed iterations")


@dataclass(frozen=True)
class TrainingResult:
    """Normalized result returned to the existing orthophoto pipeline."""

    backend: str
    ply_path: Path
    manifest_path: Path | None = None
    effective_seed: int | None = None


class TrainingBackend(Protocol):
    """Trainer implementation boundary used by DroneAI."""

    name: str

    def is_available(self) -> bool: ...

    def train(
        self,
        request: TrainingRequest,
        report_fn: ProgressCallback | None = None,
        verbose: bool = False,
    ) -> TrainingResult: ...


class LichtFeldBackend:
    """Adapter for the pinned GPL LichtFeld subprocess."""

    name = "lichtfeld"

    def __init__(self, binary: str | None = None):
        self._binary = binary

    def _resolved_binary(self) -> str | None:
        if self._binary:
            return self._binary
        from gaussian_ortho.lichtfeld_trainer import find_lichtfeld_binary

        return find_lichtfeld_binary()

    def is_available(self) -> bool:
        return self._resolved_binary() is not None

    @staticmethod
    def _to_legacy_config(request: TrainingRequest):
        from gaussian_ortho.lichtfeld_trainer import LichtFeldTrainConfig

        return LichtFeldTrainConfig(
            iterations=request.iterations,
            strategy=request.strategy,
            sh_degree=request.sh_degree,
            cap_max=request.max_cap,
            data_path=request.data_path,
            output_path=request.output_path,
            data_factor=request.resize_factor,
            max_width=request.max_width,
            tile_mode=request.tile_mode,
        )

    def build_command(self, request: TrainingRequest) -> list[str]:
        from gaussian_ortho.lichtfeld_trainer import build_lichtfeld_command

        binary = self._resolved_binary()
        if not binary:
            raise FileNotFoundError(
                "LichtFeld-Studio binary not found; set LICHTFELD_BIN"
            )
        return build_lichtfeld_command(binary, self._to_legacy_config(request))

    def train(
        self,
        request: TrainingRequest,
        report_fn: ProgressCallback | None = None,
        verbose: bool = False,
    ) -> TrainingResult:
        from gaussian_ortho.lichtfeld_trainer import train_with_lichtfeld

        binary = self._resolved_binary()
        if not binary:
            raise FileNotFoundError(
                "LichtFeld-Studio binary not found; set LICHTFELD_BIN"
            )
        ply_path = train_with_lichtfeld(
            self._to_legacy_config(request),
            report_fn=report_fn,
            verbose=verbose,
            binary=binary,
        )
        # The pinned LichtFeld CLI exposes no user-controlled global seed.
        return TrainingResult(self.name, Path(ply_path), effective_seed=None)


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

    def build_command(self, request: TrainingRequest) -> list[str]:
        binary = self._resolved_binary()
        if not binary:
            raise FileNotFoundError("DroneGS binary not found; set DRONEGS_BIN")
        manifest = Path(request.output_path) / "trainer_run.json"
        command = [
            binary,
            "--data-path", request.data_path,
            "--output-path", request.output_path,
            "--iter", str(request.iterations),
            "--strategy", request.strategy,
            "--sh-degree", str(request.sh_degree),
            "--max-cap", str(request.max_cap),
            "--resize-factor", str(request.resize_factor),
            "--max-width", str(request.max_width),
            "--tile-mode", str(request.tile_mode),
            "--seed", str(request.seed),
            "--run-manifest", str(manifest),
        ]
        tuning = request.dronegs
        command.extend(
            [
                "--optimizer-profile", tuning.optimizer_profile,
                "--pruning-policy", tuning.pruning_policy,
                "--raster-profile", tuning.raster_profile,
                "--sh-degree-interval", str(tuning.sh_degree_interval),
                "--topology-cooldown", str(tuning.topology_cooldown),
                "--photometric-finish", str(tuning.photometric_finish),
                "--photometric-mse-percent",
                str(tuning.photometric_mse_percent),
                "--prefetch-depth", str(tuning.prefetch_depth),
                "--decode-workers", str(tuning.decode_workers),
                "--jpeg-idct-scale", str(tuning.jpeg_idct_scale),
                "--test-every", str(tuning.test_every),
                "--save-eval-images", "1" if tuning.save_eval_images else "0",
            ]
        )
        return command

    def train(
        self,
        request: TrainingRequest,
        report_fn: ProgressCallback | None = None,
        verbose: bool = False,
    ) -> TrainingResult:
        if not self.is_available():
            raise FileNotFoundError("DroneGS binary not executable; set DRONEGS_BIN")
        data_path = Path(request.data_path).resolve()
        if not data_path.is_dir():
            raise FileNotFoundError(f"Data path does not exist: {data_path}")
        output_path = Path(request.output_path).resolve()
        if (
            output_path == data_path
            or data_path in output_path.parents
            or output_path in data_path.parents
        ):
            raise ValueError("DroneGS output and source dataset must be separate trees")
        if output_path.exists() and any(output_path.iterdir()):
            raise FileExistsError(f"DroneGS output directory is not empty: {output_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        command = self.build_command(request)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(self._environment),
        )
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if verbose:
                print(f"[DroneGS] {line}")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "progress" and report_fn:
                report_fn(
                    int(event.get("iteration", 0)),
                    float(event.get("loss", 0.0)),
                    int(event.get("gaussians", 0)),
                )
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"DroneGS training failed with exit code {return_code}")

        manifest_path = output_path / "trainer_run.json"
        if not manifest_path.is_file():
            raise RuntimeError("DroneGS completed without trainer_run.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("contract_version") != 1 or manifest.get("status") != "completed":
            raise RuntimeError("DroneGS returned an invalid or incomplete run manifest")
        ply_path = output_path / "point_cloud.ply"
        if not ply_path.is_file():
            raise RuntimeError("DroneGS completed without point_cloud.ply")
        return TrainingResult(
            self.name,
            ply_path,
            manifest_path=manifest_path,
            effective_seed=request.seed,
        )


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
    if selected == "lichtfeld":
        return LichtFeldBackend()
    if selected == "dronegs":
        return DroneGSBackend(environment=environment)
    raise ValueError(
        f"unknown Gaussian training backend {selected!r}; expected lichtfeld or dronegs"
    )
