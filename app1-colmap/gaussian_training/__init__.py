"""Backend-neutral Gaussian training integration for DroneAI."""

from .backends import (
    DroneGSTuning,
    TrainingBackend,
    TrainingRequest,
    TrainingResult,
    evaluate_quality_canary,
    resolve_training_backend,
    write_quality_canary,
)
from .benchmark import BenchmarkSuite, load_benchmark_suite, run_benchmark_suite

__all__ = [
    "BenchmarkSuite",
    "DroneGSTuning",
    "TrainingBackend",
    "TrainingRequest",
    "TrainingResult",
    "evaluate_quality_canary",
    "load_benchmark_suite",
    "resolve_training_backend",
    "run_benchmark_suite",
    "write_quality_canary",
]
