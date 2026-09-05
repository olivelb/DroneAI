"""Explicit per-process connection budgets; total capacity is deployment-owned."""

import os


def database_pool_options() -> dict[str, int]:
    result = {}
    for key, variable, default, minimum in (
        ("pool_size", "DRONEAI_DB_POOL_SIZE", 5, 1),
        ("max_overflow", "DRONEAI_DB_MAX_OVERFLOW", 10, 0),
        ("pool_timeout", "DRONEAI_DB_POOL_TIMEOUT_SECONDS", 30, 1),
    ):
        value = int(os.getenv(variable, str(default)))
        if not minimum <= value <= 1000:
            raise ValueError(f"{variable} must be between {minimum} and 1000")
        result[key] = value
    return result
