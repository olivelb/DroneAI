# Gaussian training backends

DroneAI now calls Gaussian trainers through `gaussian_training.backends`.
DroneGS is the production default after the dev.45 Albagnac parity gate.
The pinned LichtFeld subprocess adapter remains available as an explicit
rollback when its optional runtime is installed.

## Selection

Selection order:

1. the `trainer_backend` argument of `generate_gaussian_orthophoto()`;
2. the `gs_backend` mission parameter;
3. `DRONEAI_GAUSSIAN_BACKEND`;
4. `dronegs`.

Valid values are `lichtfeld` and `dronegs`. Binary discovery uses
`LICHTFELD_BIN` and `DRONEGS_BIN`, respectively. Selecting DroneGS before its
native executable is built fails immediately with an actionable error; there
is no silent fallback that could invalidate a benchmark.

The optional `gs_seed` mission parameter becomes the base seed. It defaults
to 42. Partition
`i` receives `gs_seed + i`.

The pipeline default also passes the validated dev.45 recipe:

- optimizer profile `dev38-staged-rotation008-absgrad050-fastgs`;
- FastGS structural rasterization and LichtFeld-compatible pruning bounds;
- progressive SH activation every 1,000 steps;
- a 1,000-step fixed-topology cooldown;
- a 1,000-step finish ramping to 100% active-pixel MSE gradient;
- 15,000 steps, 1.5 million splats, resize factor 4, and seed 42.

Every value is exposed as a mission parameter. The native CLI retains its
neutral historical defaults, so direct low-level invocations remain backward
compatible.

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
| Production default | Rollback only | Yes |
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

Build an image with the optional LichtFeld runtime, then explicitly set:

```text
DRONEAI_GAUSSIAN_BACKEND=lichtfeld
```

For the local image:

```bash
bash setup_deps.sh --with-lichtfeld
bash tools/build_local_gaussian_image.sh --with-lichtfeld
```

No model format or downstream renderer change is needed to roll back.
