# Gaussian trainer CLI contract v1

## Invocation

```text
TRAINER --data-path DATASET --output-path OUTPUT --iter ITERATIONS \
  --strategy STRATEGY --sh-degree DEGREE --max-cap COUNT \
  --resize-factor FACTOR --max-width PIXELS --tile-mode MODE \
  --seed SEED --run-manifest MANIFEST \
  [--prefetch-depth DEPTH] [--decode-workers WORKERS] \
  [--jpeg-idct-scale 0|1] \
  [--optimizer-profile PROFILE] \
  [--pruning-policy original|lichtfeld-bounds] \
  [--raster-profile auto|bounded|fastgs] \
  [--sh-degree-interval N] [--topology-cooldown N] \
  [--photometric-finish N] [--photometric-mse-percent 0..100]
```

An adapter may translate canonical options to a backend-specific spelling such
as LichtFeld's `--resize_factor`.

The pinned LichtFeld CLI does not expose a verified user-controlled global
seed or emit a v1 manifest. Its migration adapter therefore reports the seed
as ineffective and supplies no native manifest; these are known legacy gaps.

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
| `--tile-mode` | 1, 2, or 4 |
| `--seed` | Unsigned integer; mandatory for benchmarks |
| `--run-manifest` | Final manifest conforming to the v1 schema |

DroneGS accepts optional backend-specific controls. They do
not alter the mandatory canonical contract:

| Option | Constraint / default |
|---|---|
| `--prefetch-depth` | 1 through 64; default 1 |
| `--decode-workers` | 1 through 16 and no greater than depth; default 1 |
| `--jpeg-idct-scale` | 0 or 1; default 0 |
| `--optimizer-profile` | Named native schedule; default `dronegs-dev16` |
| `--pruning-policy` | `original` or `lichtfeld-bounds`; default `original` |
| `--raster-profile` | `auto`, `bounded`, or `fastgs`; default `auto` |
| `--sh-degree-interval` | Positive integer; default 1,000 |
| `--topology-cooldown` | 0 through `--iter`; default 0 |
| `--photometric-finish` | 0 through `--iter`; default 0 |
| `--photometric-mse-percent` | 0 through 100; default 0 |

Reduced-IDCT decode is experimental because libjpeg's scaled filtering changes
the RGB training target. A non-zero photometric finish requires a non-zero MSE
percentage and vice versa. Run manifests record every effective value.

The DroneAI pipeline's production profile intentionally overrides the neutral
native defaults with the validated dev.45 settings documented in
`docs/dronegs/BACKENDS.md`.

## Artifacts

Successful execution produces:

- `point_cloud.ply`, readable by DroneAI;
- optional versioned `checkpoint/` state;
- a final v1 run manifest;
- process exit status zero.

The PLY exposes positions `x/y/z`, `f_dc_0..2`, standard-order `f_rest_N`,
`scale_0..2` in log space, quaternion `rot_0..3`, and logit `opacity`.

## Progress

One compact JSON event is written per stdout line:

```json
{"event":"progress","iteration":1000,"iterations":5000,"loss":0.031,"gaussians":125000}
```

Human diagnostics go to stderr. Legacy LichtFeld output is translated by its
adapter.

## Exit status

| Status | Meaning |
|---:|---|
| 0 | Success and valid artifacts |
| 2 | Invalid arguments or incompatible dataset |
| 3 | CUDA unavailable |
| 4 | GPU out of memory |
| 5 | Cancelled cleanly |
| 10 | Training/internal failure |

SIGTERM stops new work, atomically completes or invalidates the active
checkpoint, writes a cancelled manifest when possible, and exits 5.
