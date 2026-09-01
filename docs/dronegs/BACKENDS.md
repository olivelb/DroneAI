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

`DRONEGS_PRODUCTION_PROFILE_V1` is the immutable production recipe. It uses:

- optimizer profile `reference-absolute`;
- structural FastGS buckets/checkpoints and warp-cooperative backward;
- bounded spatial pruning and in-place slot recycling;
- progressive SH activation every 1,000 steps;
- color SH with scalar opacity (opacity-SH-v1 is opt-in and selects `custom`);
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

The platform Production V1 recipe atomically replaces `training.ckpt` every
7,500 steps for its 15,000-step budget. Other platform budgets limit intermediate
checkpoints to one/two/three according to run duration. The standalone native
CLI defaults to `--checkpoint-every 0` (disabled); it is not the platform policy.
Checkpoint format V5 retains V4 checksum/fsync/atomic publication and adds
initial pixel-weighted PSNR/SSIM metadata. V4 remains readable and carries
the opacity-SH Adam moments. V1-V3 checkpoints are rejected because their raw
Gaussian layout is not resume-compatible; a retained final PLY can still be
used as `--initial-ply`.
With opacity-SH disabled, dev.66 does not allocate its device gradients or Adam
moments. V4/V5 retain their wire layout: inactive moments are serialized as zeros
on the host and consumed without device allocation on restore. This saves
180 bytes per allocated Gaussian slot; the Gaussian AoS and PLY layout do not change.
The checkpoint contains:

- all Gaussian parameters, including opacity-SH-v1 residuals;
- all Adam first/second moments and beta powers;
- densification statistics and topology counters;
- active SH degree and schedule state;
- optimizer step, deterministic seed and scene/config fingerprints.

The worker stores recovery state outside the disposable mission workspace and
uploads every native `checkpoint_saved` event under the mission's
`gaussian-checkpoints/` S3 prefix. A replacement pod restores that prefix
before training and automatically supplies `--resume-from` when it finds an
incomplete run. Dataset and training-configuration fingerprints must match or
the native trainer refuses the checkpoint.

The adapter polls cancellation every 250 ms even when DroneGS is silent. It
sends SIGTERM to the complete process group, waits up to ten seconds, then
sends SIGKILL. The latest atomic local/S3 checkpoint is preserved. Recovery
state is deleted only after the PLY, strict manifest, recorded canary and final
Gaussian artifacts have all been promoted.

Completed-result reuse additionally requires the current dataset fingerprint,
trainer binary SHA-256, exact requested and effective training profiles, PLY
size and PLY SHA-256. Canary thresholds are
acceptance policy rather than training compatibility: changing only a
threshold re-evaluates the persisted metrics without retraining or rejecting
the completed model.

## Canary contract

Production V1 reserves cameras where
`scene_index % gs_test_every == 0` (`gs_test_every=8`). Training must produce a
completed manifest and evaluates it against both configured thresholds:

- `gs_canary_min_psnr=18.0`;
- `gs_canary_min_ssim=0.25`.

The adapter atomically writes `canary_result.json`. A failed resident-cell
canary is a structured quality warning: the cell label, failed metrics,
measurements and thresholds are retained in the training artifact and stage
quality metrics, while later cells and downstream stages continue. Training
or artifact-integrity failures remain blocking. Completed warned cells are
durable recovery points and are not retrained or revalidated on process
restart.

The modulo split remains useful for frozen benchmark parity but is correlated
for sequential flights. Custom profiles can select deterministic
`spatial-block` evaluation and a guard ring excluded from training; manifests
record training, held-out and ignored counts. This is not silently substituted
into production V1 because doing so would invalidate the accepted dev.45
metric baseline. ALBAGNAC and SAVERES comparison runs remain the gate for a
future immutable V2 threshold.

Seeded schedules and checkpoint parity are not a cross-GPU bitwise guarantee.
Floating-point atomics and CUDA fast math can change accumulation order or
rounding across architectures, potentially changing topology near a threshold.
Promotion requires the exact source/binary identity, frozen dataset/profile,
target GPU and rendered quality metrics; historical gates do not qualify a new HEAD.

## Source safety

The input COLMAP tree and output tree must be disjoint. Container launchers
mount source datasets read-only, and the native trainer writes only below the
explicit output directory.
