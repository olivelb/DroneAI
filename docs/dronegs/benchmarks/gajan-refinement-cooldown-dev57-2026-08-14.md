# GAJAN refinement-statistics cooldown — dev.57

Date: 2026-08-14

GPU: NVIDIA RTX 3090 24 GiB

Dataset: GAJAN R2S, 438 registered views (382 train, 56 held out)

Raster profile: structural FastGS, tile mode 4, SH3

Seed: 42

## Change under test

The control is the merged `0.5.0-dev.56` binary. The candidate stops
computing refinement-only SSIM error/Sobel maps, frame refinement weights,
visibility, edge and absolute-gradient statistics once no later scheduled
topology refinement can consume them. Geometry gradients, color/opacity/SH
gradients and all Adam updates remain active through the final iteration.

No operator profile, objective weight, topology schedule, image split or
capacity was changed. All retained run directories are under:

```text
/home/olivier/benchmarks/gajan-refinement-cooldown-dev57-20260814
```

## Fixed-topology micro A/B

The micro-benchmark reloads the retained 191,686-Gaussian dev.56 Normal PLY,
runs 100 optimizer steps in full cooldown, and therefore isolates work that
cannot feed another topology refinement. Two alternating control/candidate
pairs were retained.

| Variant | Mean training (s) | Mean wall (s) | Final PSNR (dB) | Final SSIM |
|---|---:|---:|---:|---:|
| dev.56 control | 0.3990 | 2.6718 | 18.85698 | 0.37414664 |
| dev.57 candidate | 0.3417 | 2.6257 | 18.85697 | 0.37414624 |

Training improves by **14.4%** in the isolated fixed-topology section. The
remaining wall time is dominated by dataset/image startup and held-out
evaluation. Final loss, PSNR and SSIM agree within normal floating-point run
variation.

## Complete profile A/B

Each profile was repeated twice from the same COLMAP input and seed. Values
below are two-run means.

| Profile | Variant | Wall (s) | Training (s) | Gaussians | PSNR (dB) | Pixel PSNR (dB) | SSIM |
|---|---|---:|---:|---:|---:|---:|---:|
| Fast 7,500 | dev.56 | 20.896 | 17.337 | 54,889 | 16.8292 | 14.4478 | 0.311358 |
| Fast 7,500 | dev.57 | 20.625 | 17.229 | 54,890 | 16.8057 | 14.4344 | 0.311097 |
| Normal 15,000 | dev.56 | 58.948 | 55.307 | 191,547 | 18.9800 | 16.4772 | 0.374084 |
| Normal 15,000 | dev.57 | 58.849 | 55.178 | 191,438 | 18.9667 | 16.4691 | 0.374123 |

The complete-profile gain is deliberately smaller because Fast skips the
refinement-only work for its final 1,100 steps and Normal for its final 1,000
steps. Fast training improves by **0.6%** and wall time by **1.3%**. Normal
training improves by **0.2%** and wall time by **0.2%**. Held-out changes are
at most 0.024 dB PSNR and 0.00027 SSIM, and population changes remain below
0.1%; these are inside the retained two-run variation.

## Verification and decision

- Eight of eight native CPU/CUDA CTests pass in the production CUDA image on
  the RTX 3090.
- A pure schedule regression proves collection stops immediately after the
  last possible 200-step refinement consumer.
- A CUDA one-step regression proves collect and skip modes produce the same
  loss and updated Gaussian model.
- All four full-profile manifests report `completed`; no artifact was removed.

**Decision:** accept dev.57. It removes dead lifecycle work without changing
the training contract. The improvement is material in a fixed-topology phase
but should be described as a small end-to-end speedup for the current Fast and
Normal profiles.
