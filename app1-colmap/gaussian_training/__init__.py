"""Backend-neutral Gaussian training integration for DroneAI."""

from .backends import (
    TrainingBackend,
    TrainingRequest,
    TrainingResult,
    resolve_training_backend,
)
from .benchmark import BenchmarkSuite, load_benchmark_suite, run_benchmark_suite

__all__ = [
    "BenchmarkSuite",
    "TrainingBackend",
    "TrainingRequest",
    "TrainingResult",
    "load_benchmark_suite",
    "resolve_training_backend",
    "run_benchmark_suite",
]
