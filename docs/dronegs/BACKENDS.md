# DroneGS backend boundary

DroneGS is the sole executable Gaussian-training backend. The mission
pipeline, local runner and container image all resolve to the contract-v1
DroneGS process; there is no runtime fallback or alternate trainer selector.

Historical LichtFeld measurements remain in benchmark reports because they
are the frozen quality/speed control used during development. LichtFeld source
references also remain in the GPL provenance register for the DroneGS files
that adapt GPL-covered behavior. Neither use requires a LichtFeld checkout,
binary, container, Python adapter or vcpkg installation.

## Selection and discovery

`gs_backend` accepts only `dronegs`. Binary discovery uses `DRONEGS_BIN`, then
the repository build, then `PATH`. Unknown values fail before a mission starts
instead of silently choosing a fallback.

The production profile uses:

- optimizer profile `reference-absolute`;
- structural FastGS buckets/checkpoints and warp-cooperative backward;
- bounded spatial pruning and in-place slot recycling;
- progressive SH activation every 1,000 steps;
- a 1,000-step fixed-topology cooldown;
- a 1,000-step objective ramp ending at 100% active-pixel MSE;
- 15,000 steps, 1.5 million splats, resize factor 4 and seed 42.

## Stable Python boundary

```text
orthophoto pipeline
        |
        v
TrainingRequest
        |
        +------ DroneGSBackend -------- CLI v1 / manifest v1 / standard PLY
        |
        v
TrainingResult -> canary gate -> existing GaussianModel
```

The partition, merge, filtering, geo-alignment, rendering and GeoTIFF stages
remain downstream of this boundary.

## Recovery contract

By default, DroneGS atomically replaces `training.ckpt` every 2,000 steps.
The checkpoint contains:

- all Gaussian parameters;
- all Adam first/second moments and beta powers;
- densification statistics and topology counters;
- active SH degree and schedule state;
- optimizer step, deterministic seed and scene/config fingerprints.

If a mission is restarted with an incomplete output directory and a checkpoint
but no completed `trainer_run.json`, the pipeline automatically supplies
`--resume-from`. Dataset and training-configuration fingerprints must match or
the native trainer refuses the checkpoint. A deliberately paused native
canary exits with code 75 after writing a checkpoint; ordinary completed jobs
exit zero. After a completed model passes its quality canary, the Python
pipeline removes the large optimizer checkpoint; the PLY, manifest, canary and
evaluation artifacts become the compact durable recovery point.

## Canary contract

The production split reserves cameras where
`scene_index % gs_test_every == 0` (`gs_test_every=8`). Training must produce a
completed manifest and meet both configured thresholds:

- `gs_canary_min_psnr=18.0`;
- `gs_canary_min_ssim=0.35`.

The adapter atomically writes `canary_result.json`. A failed canary stops the
orthomosaic pipeline while preserving the PLY, manifest, evaluation pairs and
checkpoint for diagnosis.

## Source safety

The input COLMAP tree and output tree must be disjoint. Container launchers
mount source datasets read-only, and the native trainer writes only below the
explicit output directory.
