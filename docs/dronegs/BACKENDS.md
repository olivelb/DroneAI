# Gaussian training backends

DroneAI now calls Gaussian trainers through `gaussian_training.backends`.
The default remains the pinned LichtFeld subprocess, so upgrading to Phase 2
does not change production training unless a backend is explicitly selected.

## Selection

Selection order:

1. the `trainer_backend` argument of `generate_gaussian_orthophoto()`;
2. the `gs_backend` mission parameter;
3. `DRONEAI_GAUSSIAN_BACKEND`;
4. `lichtfeld`.

Valid values are `lichtfeld` and `dronegs`. Binary discovery uses
`LICHTFELD_BIN` and `DRONEGS_BIN`, respectively. Selecting DroneGS before its
native executable is built fails immediately with an actionable error; there
is no silent fallback that could invalidate a benchmark.

The optional `gs_seed` mission parameter becomes the base seed. Partition
`i` receives `gs_seed + i`.

## Stable Python boundary

`TrainingRequest` validates the contract-v1 inputs before a backend starts.
`TrainingResult` always identifies the backend and exported PLY, and optionally
exposes the native manifest and effective seed.

```text
orthophoto pipeline
        |
        v
TrainingRequest
        |
        +------ LichtFeldBackend ------ legacy CLI / standard PLY
        |
        +------ DroneGSBackend -------- CLI v1 / manifest v1 / standard PLY
        |
        v
TrainingResult -> existing GaussianModel
```

The partition, merge, filtering, geo-alignment, rendering, and GeoTIFF stages
are deliberately downstream of this boundary and remain shared.

## Capability differences during migration

| Capability | LichtFeld adapter | DroneGS adapter |
|---|---|---|
| Production default | Yes | No |
| CLI spelling | Legacy `--resize_factor` | Canonical `--resize-factor` |
| Required run manifest | No | Yes |
| User-controlled seed | Not exposed by pinned CLI | Required |
| Progress | Legacy stdout/MCP parser | JSON Lines |
| PLY output | Required | Required |

The requested seed is recorded by the benchmark harness for every backend,
but `effective_seed` is unknown for the pinned LichtFeld adapter. Quality and
timing comparisons must therefore report this limitation until LichtFeld has
a verified deterministic seed control.

## Rollback

Unset `DRONEAI_GAUSSIAN_BACKEND` and omit `gs_backend`, or explicitly set:

```text
DRONEAI_GAUSSIAN_BACKEND=lichtfeld
```

No model format or renderer change is needed to roll back.
