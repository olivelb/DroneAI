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

## Cell 2 fixed/adaptive crop A/B

A controlled replay used the same frozen cell-2 workspace, trainer binary,
dataset fingerprint, 30,000 iterations, 1.7 million Gaussian cap and all other
scientific parameters. Only the native crop partition policy changed. The
machine-readable comparison is retained beside the run artifacts as
`fixed-vs-adaptive-c0c1c56-1p7m.json`.

| Crop policy | Training descriptors | Held-out descriptors | PSNR | SSIM | Pixel-weighted PSNR | Pixel-weighted SSIM | Wall time |
|---|---:|---:|---:|---:|---:|---:|---:|
| fixed 4 x 4 | 485 | 64 | 17.4448 | 0.5028 | 17.2136 | 0.5358 | 45 min 06 s |
| adaptive, up to 4 x 4 | 343 | 36 | 18.0051 | 0.5271 | 17.3447 | 0.5408 | 48 min 17 s |

The adaptive policy improves equal-view PSNR by 0.5604 dB and just clears the
18 dB canary, but pixel-weighted PSNR improves by only 0.1311 dB and remains
below 18 dB. It also increases wall time by 191 seconds in this cache-limited
run. Adaptive crops are therefore useful but are not, by themselves, a
sufficient qualification result for the weakest cell.

Both runs used the exact native executable SHA-256
`3f336ab3ed535e5e40b3b3574e04f7b20ba22a118bb06246a38fb72687e2f031`.
Their point clouds have different hashes, as expected for different frame
partitions; neither output is presented as binary-parity evidence.

## Restricted-track invariant found during diagnosis

The retained workspaces exposed a seed-selection defect: track length had been
validated on the complete COLMAP model before observations were restricted to
the cameras of a resident cell. This allowed globally multi-view points to
become mono-view seeds inside a cell. The proportions were 11.5% for cell 0,
13.2% for cell 1 and 15.6% for cell 2.

The exporter now applies the minimum-track gate after camera restriction and
after checking that every retained 2D observation falls inside the native crop
actually used for training. It records both the resulting track distribution
and the crop-rejected observation count. Historical workspaces and their A/B
outputs remain frozen. A new cell-2 replay must isolate this corrected seed
contract before increasing Gaussian capacity.

The retained subsets confirm that cell 2 does not lack source photos. It has
more selected views than cells 0 and 1, but less useful projected coverage and
substantially weaker multi-view redundancy:

| Cell | Selected photos | Mean native crop | Crops below 10% | Sparse points | Mean track | Track >= 3 | Track >= 5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | 118 | 75.04% | 11 | 107,952 | 3.249 | 67.79% | 15.90% |
| 1 | 118 | 68.15% | 8 | 123,826 | 3.686 | 64.41% | 24.34% |
| 2 | 123 | 63.22% | 16 | 77,054 | 2.611 | 55.83% | 4.13% |

This distinguishes raw image count from usable cross-view evidence. Raising
the Gaussian cap cannot repair missing overlap by itself, so capacity is only
the next variable after the corrected-track replay.

## Controlled follow-up

The following sequence is required before resuming the full six-cell product:

1. regenerate cell 2 with the corrected restricted-track invariant and replay
   the adaptive policy at exactly 1.7 million Gaussians;
2. compare against the frozen adaptive result, recording seed-track
   distribution, frame counts, PLY, loss, PSNR/SSIM, VRAM and wall time;
3. run `reference-absolute-absgrad025` at the same capacity only if the seed
   correction does not provide a stable quality margin;
4. if detail remains below the gate, run the same reference recipe at the
   8 GiB resident hard cap (2.3 million for this envelope);
5. promote a change only if the held-out gate, pixel-weighted metrics, visual
   detail, VRAM and wall time improve without weakening another cell;
6. resume cells 3–5, then verify all canaries, filtering, resident density,
   core/buffer seams and final facade comparisons against the reference raster
   and colored dense point cloud.

All intermediate artifacts are retained. No failed result is published as a
qualified profile or exposed as a production default.
