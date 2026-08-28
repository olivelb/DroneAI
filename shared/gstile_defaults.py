"""Versioned GSTile production policy; importable before NumPy initialization."""
from __future__ import annotations

import os
from typing import Literal

GSTILE_DEFAULTS_PROFILE = "gstile-qualified-2026-08-28"
GSTILE_LOD_PROXY_SIZE = 16_384
GSTILE_LOD_PROXY_STRATEGY: Literal["adaptive-moment"] = "adaptive-moment"
GSTILE_PACK_TARGET_BYTES = 2 * 1024**2
GSTILE_PACK_WORKERS = 2
GSTILE_PACK_PENDING_BYTES = 128 * 1024**2


def configure_gstile_process() -> None:
    """Only call in a dedicated GSTile process, before importing numerical code."""
    # Cycle-counter exponent, not milliseconds. Explicit values (even 0) win.
    os.environ.setdefault("OPENBLAS_THREAD_TIMEOUT", "16")
