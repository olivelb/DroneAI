"""Single source of truth for the validated DroneGS production recipe."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

DRONEGS_PRODUCTION_PROFILE_ID = "DRONEGS_PRODUCTION_PROFILE_V1"


@dataclass(frozen=True)
class DroneGSProductionProfile:
    """Immutable parameters accepted by the Albagnac dev.45 benchmark."""

    profile_id: str = DRONEGS_PRODUCTION_PROFILE_ID
    backend: str = "dronegs"
    iterations: int = 15_000
    data_factor: int = 4
    max_width: int = 1_600
    tile_mode: int = 4
    cap_max: int = 1_500_000
    sh_degree: int = 3
    seed: int = 42
    optimizer_profile: str = "reference-absolute"
    pruning_policy: str = "spatial-bounds"
    raster_profile: str = "fastgs"
    sh_degree_interval: int = 1_000
    topology_cooldown: int = 1_000
    photometric_finish: int = 1_000
    photometric_mse_percent: int = 100
    checkpoint_every: int = 2_000
    test_every: int = 8
    test_split: str = "modulo"
    test_guard_percent: int = 0
    canary_min_psnr: float = 18.0
    canary_min_ssim: float = 0.25

    def pipeline_defaults(self) -> dict[str, Any]:
        """Return mission parameter names with API-compatible string values."""

        return {
            "gs_backend": self.backend,
            "gs_iterations": str(self.iterations),
            "gs_data_factor": str(self.data_factor),
            "gs_max_width": str(self.max_width),
            "gs_tile_mode": str(self.tile_mode),
            "gs_cap_max": str(self.cap_max),
            "gs_sh_degree": str(self.sh_degree),
            "gs_seed": str(self.seed),
            "gs_production_profile": self.profile_id,
            "gs_optimizer_profile": self.optimizer_profile,
            "gs_pruning_policy": self.pruning_policy,
            "gs_raster_profile": self.raster_profile,
            "gs_sh_degree_interval": str(self.sh_degree_interval),
            "gs_topology_cooldown": str(self.topology_cooldown),
            "gs_photometric_finish": str(self.photometric_finish),
            "gs_photometric_mse_percent": str(
                self.photometric_mse_percent
            ),
            "gs_checkpoint_every": str(self.checkpoint_every),
            "gs_test_every": str(self.test_every),
            "gs_test_split": self.test_split,
            "gs_test_guard_percent": str(self.test_guard_percent),
            "gs_canary_min_psnr": str(self.canary_min_psnr),
            "gs_canary_min_ssim": str(self.canary_min_ssim),
        }

    def trainer_parameters(self) -> dict[str, Any]:
        """Return the exact trainer-side parameters used by production."""

        values = asdict(self)
        values.pop("backend")
        return values


DRONEGS_PRODUCTION_PROFILE_V1 = DroneGSProductionProfile()
DRONEGS_PRODUCTION_DEFAULTS = MappingProxyType(
    DRONEGS_PRODUCTION_PROFILE_V1.pipeline_defaults()
)


def effective_raster_profile(
    requested: str,
    optimizer_profile: str,
) -> str:
    """Resolve the native ``auto`` raster choice to its executed backend."""

    if requested in {"bounded", "fastgs"}:
        return requested
    return (
        "fastgs"
        if optimizer_profile == "dev38-staged-rotation008-absgrad050-fastgs"
        else "bounded"
    )
