# Saint-Étienne facade resident HD v2 qualification — 2026-08-13

## Status

**Not qualified.** The first six-cell resident run stopped at the strict
held-out canary for cell 2. Products were therefore not merged or promoted.
The retained cells and failed-cell evidence are a reproducible hard-block A/B
dataset; this report must not be read as a production acceptance record.

## Scope

- source workspace: Saint-Étienne Mavic 3E facade acquisition;
- product: local metric facade orthophoto and depth, without EPSG CRS;
- GPU: NVIDIA RTX 3090 24 GiB;
- logical capacity envelope: 8 GiB;
- trainer: DroneGS `0.5.0-dev.48`, CUDA 12.9;
- facade profile: `DRONEGS_FACADE_HD_V2`;
- 30,000 iterations, native 4096 px crops, tile mode 4, SH degree 3;
- adaptive resident scene: 6 core/buffer cells, 1.7 million Gaussians per
  resident buffer, 5 million unique-scene floor;
- output sampling request: 1 cm/px in a local metric facade frame.

The surveyed wall plane was approximately 307 m². The GSD-derived retained
surface target was only 300,000 Gaussians, so the profile's 5 million scene
floor—not the geometric GSD formula—controlled this run.

## Resource evidence

Training remained compute-bound and did not approach the logical memory
envelope. Observed cell training used approximately 4.58 GiB VRAM at 98–99%
GPU utilization on the RTX 3090. No CUDA error or OOM occurred.

## Held-out evidence

| Cell | Sparse seeds | Resident Gaussians | PSNR (equal view) | SSIM (equal view) | Pixel-weighted PSNR | Pixel-weighted SSIM | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 69,793 | 1,700,000 | 20.7012 | 0.5776 | 19.9058 | 0.5984 | passed |
| 1 | 73,559 | 1,700,000 | 22.1731 | 0.6618 | 21.8465 | 0.6507 | passed |
| 2 | 27,877 | 1,700,000 | 17.4934 | 0.5032 | 17.2072 | 0.5356 | **failed PSNR** |

The immutable facade canary thresholds were PSNR >= 18 dB and SSIM >= 0.25.
The pixel-weighted values were reconstructed from the exact persisted target /
prediction pairs. Cell 2 remains below the PSNR threshold after pixel
weighting, so small edge crops did not cause a false rejection; equal-view
averaging had slightly hidden the weakness.

Visual inspection of the lowest-scoring held-out pair showed strongly smoothed
stone texture and a color shift. The failure is therefore a real local
generalization/detail defect, not a threshold-only or resource defect.

## Controlled follow-up

The following sequence is required before resuming the full six-cell product:

1. replay cell 2 with the current native binary and the exact 1.7 million
   baseline recipe;
2. compare the current binary against the retained dev.48 run, recording PLY,
   loss, PSNR/SSIM and trajectory differences rather than assuming native
   refactors are numerically neutral;
3. run `reference-absolute-absgrad025` at the same capacity;
4. if detail remains below the gate, run the same reference recipe at the
   8 GiB resident hard cap (2.3 million for this envelope);
5. promote a change only if the held-out gate, pixel-weighted metrics, visual
   detail, VRAM and wall time improve without weakening another cell;
6. resume cells 3–5, then verify all canaries, filtering, resident density,
   core/buffer seams and final facade comparisons against the reference raster
   and colored dense point cloud.

All intermediate artifacts are retained. No failed result is published as a
qualified profile or exposed as a production default.
