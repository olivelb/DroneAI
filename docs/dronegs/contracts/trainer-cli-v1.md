# Gaussian trainer CLI contract v1

## Invocation

```text
TRAINER --data-path DATASET --output-path OUTPUT --iter ITERATIONS \
  --strategy STRATEGY --sh-degree DEGREE --max-cap COUNT \
  --resize-factor FACTOR --max-width PIXELS --tile-mode MODE \
  --seed SEED --run-manifest MANIFEST \
  [--profile-id NAME] [--dataset-fingerprint VALUE] \
  [--prefetch-depth DEPTH] [--decode-workers WORKERS] \
  [--jpeg-idct-scale 0|1] \
  [--optimizer-profile PROFILE] \
  [--pruning-policy original|spatial-bounds] \
  [--raster-profile auto|bounded|fastgs] \
  [--sh-degree-interval N] [--topology-cooldown N] \
  [--photometric-finish N] [--photometric-mse-percent 0..100] \
  [--test-every 0|N] \
  [--test-split modulo|spatial-block] \
  [--test-guard-percent 0..100] [--save-eval-images 0|1] \
  [--checkpoint-every N] [--checkpoint-path PATH] \
  [--resume-from PATH] [--stop-after N]
```

DroneGS is the sole implementation of this contract.

## Arguments

| Option | Constraint |
|---|---|
| `--data-path` | Read-only COLMAP root with images and sparse model |
| `--output-path` | New or explicitly reusable output directory |
| `--iter` | Integer greater than zero |
| `--strategy` | Initially `mrnf` |
| `--sh-degree` | 0 through 3 |
| `--max-cap` | Integer greater than zero |
| `--resize-factor` | 1, 2, 4, or 8 |
| `--max-width` | 1 through 4096 |
| `--tile-mode` | 1, 2, or 4 source-image training views |
| `--seed` | Unsigned integer; mandatory for benchmarks |
| `--run-manifest` | Final manifest conforming to the native v1 schema |
| `--profile-id` | Versioned recipe identifier; production uses `DRONEGS_PRODUCTION_PROFILE_V1` |
| `--dataset-fingerprint` | Optional external content identity used by distributed resume |

DroneGS accepts optional backend-specific controls. They do
not alter the mandatory canonical contract:

| Option | Constraint / default |
|---|---|
| `--prefetch-depth` | 1 through 64; default 1 |
| `--decode-workers` | 1 through 16 and no greater than depth; default 1 |
| `--jpeg-idct-scale` | 0 or 1; default 0 |
| `--optimizer-profile` | Named native schedule; production uses `reference-absolute` |
| `--pruning-policy` | `original` or `spatial-bounds`; production uses `spatial-bounds` |
| `--raster-profile` | `auto`, `bounded`, or `fastgs`; default `auto` |
| `--sh-degree-interval` | Positive integer; default 1,000 |
| `--topology-cooldown` | 0 through `--iter`; default 0 |
| `--photometric-finish` | 0 through `--iter`; default 0 |
| `--photometric-mse-percent` | 0 through 100; default 0 |
| `--test-every` | 0 or at least 2; production uses 8 |
| `--test-split` | `modulo` or `spatial-block`; immutable production V1 uses `modulo` |
| `--test-guard-percent` | 0 through 100; non-zero only with `spatial-block` and an enabled split |
| `--save-eval-images` | 0 or 1; requires a held-out split |
| `--checkpoint-every` | 0 disables periodic saves; production uses 2,000 |
| `--checkpoint-path` | Required when checkpointing or deliberately pausing |
| `--resume-from` | Existing compatible full-state checkpoint |
| `--stop-after` | Canary-only deliberate pause step, no greater than `--iter` |

Reduced-IDCT decode is experimental because libjpeg's scaled filtering changes
the RGB training target. A non-zero photometric finish requires a non-zero MSE
percentage and vice versa. Run manifests record requested and effective raster
profiles as separate, uniquely named fields. The adapter rejects duplicate
JSON keys, validates the contract, records the trainer binary SHA-256, and
promotes the PLY only after recording its SHA-256.

Tile mode is an image-space training contract, not a scene partition. Mode `1`
uses the complete source image, mode `2` bisects its longest axis, and mode `4`
uses a 2-by-2 grid. Each crop is decoded independently, `--max-width` applies
to the crop rather than the full photograph, and the camera principal point is
translated into crop coordinates before rasterization. Dataset splitting is
performed on source photographs before expansion, so all tiles from one photo
remain together in training, held-out evaluation, or the spatial guard set.
The reduction after JPEG decode uses area resampling with exact fractional
source-pixel coverage; it no longer uses nearest-neighbour or bilinear point
sampling. Images that already match the requested dimensions are not filtered.

A partition subset may include `image_regions.tsv` at its dataset root. The
first line must be `# dronegs-image-regions-v1`; each following tab-separated
row is `image_name`, `source_x`, `source_y`, `width`, `height`, with right and
bottom exclusive. Names must be unique and every region must lie inside its
COLMAP camera dimensions. DroneGS composes `tile_mode` inside this base region,
decodes directly from the untouched source JPEG, and translates the principal
point by the combined source offset. Dataset fingerprints v3 include this
contract, so a crop change invalidates checkpoint and completed-run reuse.

`spatial-block` computes camera centers from COLMAP world-to-camera poses,
selects the two dominant spatial axes, reserves a deterministic central block
with the same target count as `--test-every`, then excludes the requested guard
ring from training. This evaluates geographic generalization without changing
the immutable modulo-based V1 comparison baseline.

The native process contract is described by
[`trainer-run-v1.schema.json`](trainer-run-v1.schema.json). After a successful
run, the production adapter adds strict binary and PLY identities and validates
the completed artifact against
[`../../../app1-colmap/dronegs/schema/trainer_run.schema.json`](../../../app1-colmap/dronegs/schema/trainer_run.schema.json).

The DroneAI pipeline's production profile intentionally overrides the neutral
native defaults with immutable `DRONEGS_PRODUCTION_PROFILE_V1`, derived from
the dev.45 acceptance settings and hardened through dev.47 as documented in
`docs/dronegs/BACKENDS.md`.

## Artifacts

Successful execution produces:

- `point_cloud.ply`, readable by DroneAI;
- optional atomic versioned `training.ckpt` full state;
- a final v1 run manifest;
- process exit status zero.

The PLY exposes positions `x/y/z`, `f_dc_0..2`, standard-order `f_rest_N`,
directional opacity-logit residuals `opacity_sh_N`, `scale_0..2` in log space,
quaternion `rot_0..3`, and the view-independent logit `opacity`. The opacity
properties use coefficients 1 through 15 of the same SH basis and degree as
color; degree zero emits no `opacity_sh_N` properties. This scoped
`opacity-SH-v1` contract does not make scale or rotation view-dependent.

Checkpoint format V5 preserves initial pixel-weighted metrics and opacity-SH
optimizer moments; V4 remains readable. Checkpoints from V1 through V3 are
deliberately rejected because their raw Gaussian layout
cannot be resumed safely; their final PLY remains a valid initialization input.

## Progress

One compact JSON event is written per stdout line:

```json
{"event":"progress","iteration":1000,"iterations":5000,"loss":0.031,"gaussians":125000}
```

Human diagnostics go to stderr.

## Exit status

| Status | Meaning |
|---:|---|
| 0 | Success and valid artifacts |
| 2 | Invalid arguments or incompatible dataset |
| 3 | CUDA unavailable |
| 4 | GPU out of memory |
| 5 | Cancelled cleanly |
| 10 | Training/internal failure |
| 75 | Deliberately paused after an atomic checkpoint |

The DroneAI adapter handles cancellation: it sends SIGTERM to the DroneGS
process group, waits ten seconds, then sends SIGKILL if required. The latest
already-published atomic checkpoint remains the recovery boundary.
