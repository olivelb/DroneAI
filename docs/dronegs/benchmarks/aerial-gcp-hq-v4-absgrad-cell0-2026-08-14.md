# Aerial GCP HQ v4 AbsGrad cell-0 qualification — 2026-08-14

## Verdict

AbsGrad with a score threshold of `0.50` improves the representative aerial
cell while keeping the same 5.1 M resident Gaussian population and effectively
the same runtime. The clean current-main dev.57 replay is the authoritative
comparison: relative to its same-binary absolute-gradient control, mean PSNR
increases by 0.149 dB and mean SSIM by 0.02127. All five held-out views improve
in SSIM and four of five improve in PSNR.

The experiment is therefore a positive **single-cell scientific gate** for the
HQ v4 candidate. It is not yet a full-scene product qualification: the
multi-cell aggregation, RGB/height GeoTIFF publication, density and seam gates
must still be exercised with the current trainer before the profile is promoted
as the default HQ path.

Visual inspection agrees with the metrics. The candidate is more coherent and
less over-sharpened than the reference on fine vegetation. It contains no hole
or black region. A horizontal texture/tonal transition remains visible in the
representative view, so cell overlap and final compositing remain explicit
follow-up gates.

## Initial dev.49 controlled scope

| Item | Reference | Candidate |
| --- | --- | --- |
| Host / GPU | BIGZEN / RTX 3090 24 GiB | same |
| Native binary | DroneGS dev.49 | exact same binary |
| Binary SHA-256 | `ced01d5dfe56975d14ec51fa1e36c5012c92f0ec565bef174599a151fda2a700` | same |
| Source state | `c0c1c56cdee9d957d2449991cf569e7566cce23f-dirty` | same |
| Dataset | aerial GCP, cell 0 | same fingerprint and split |
| Source / training views | 23 / 46 | 23 / 46 |
| Held-out views | 5 | same five views |
| Iterations | 30,000 | 30,000 |
| Gaussian ceiling | 5.1 M | 5.1 M |
| Seed | 42 | 42 |
| Profile | `reference-absolute` | `reference-absolute-absgrad050` |
| AbsGrad score threshold | disabled | `0.50` |

The candidate ran in container
`dronegs-hq-v4-cell0-absgrad050-r2-20260814`, from
`2026-08-14T16:18:36Z` to `2026-08-14T17:52:17Z`. It exited with code zero,
without OOM or runtime error.

## Initial dev.49 quality and runtime

| Metric | Reference | AbsGrad 0.50 | Delta |
| --- | ---: | ---: | ---: |
| Final loss | 0.0227475 | 0.0228429 | +0.42% |
| Mean PSNR | 21.7076 dB | 21.9312 dB | **+0.2236 dB** |
| Pixel-weighted PSNR | 21.0054 dB | 21.2008 dB | **+0.1954 dB** |
| Mean SSIM | 0.461932 | 0.482792 | **+0.020861 / +4.52%** |
| Pixel-weighted SSIM | 0.478607 | 0.499640 | **+0.021033 / +4.39%** |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Native training | 5,583.97 s | 5,600.65 s | +16.68 s / +0.30% |
| Wall time | 5,594.83 s | 5,620.60 s | +25.77 s / +0.46% |
| Periodic checkpoints | 224.03 s | 224.43 s | +0.40 s |
| Topology refinement | 113.54 s | 115.30 s | +1.76 s |

The final scalar training loss is marginally higher, but it is a stochastic
batch value rather than the held-out product metric. The consistent PSNR and
SSIM improvement across all five evaluation views is the acceptance evidence.

| Held-out view | Reference PSNR | Candidate PSNR | Reference SSIM | Candidate SSIM |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 23.5327 | **23.7447** | 0.55158 | **0.56896** |
| 1 | 19.6821 | **19.7442** | 0.36037 | **0.37799** |
| 2 | 23.9581 | **24.1948** | 0.55046 | **0.56920** |
| 3 | 19.0229 | **19.2747** | 0.44578 | **0.47386** |
| 4 | 22.3420 | **22.6976** | 0.40146 | **0.42394** |

The candidate reached the 5.1 M ceiling near iteration 14,200. A small
subsequent prune/regrow cycle is expected topology maintenance; the population
returned to the ceiling and stayed stable through iteration 30,000. The run
added 6,110,511 Gaussians, pruned 1,018,841 and reused 961,159 slots.

## Clean current-main dev.57 replay

The dev.49 experiment was repeated from a clean checkout of current `main` at
`6c18c9eb22e201c513f2fdcdac535f8f2d183153`. The build targeted the RTX 3090
directly with CUDA architecture `86`; all eight CPU/CUDA CTests passed before
the long runs. The exact dev.57 binary has SHA-256
`4d17b2b3b8df669ac696a99f78695e9675204b73c9f7ea9ca6d0acafbc04f62f`.

The first replay ran AbsGrad 0.50. A second run used `reference-absolute` with
the exact same binary, dataset fingerprint, train/test split, seed, 30,000-step
schedule, 5.1 M capacity and all other parameters. This same-binary control is
required because comparing dev.57 directly with a dev.49 model would confound
the densification choice with the accepted trainer optimizations.

| Metric | dev.57 control | dev.57 AbsGrad 0.50 | Delta |
| --- | ---: | ---: | ---: |
| Final loss | 0.0229824 | 0.0226593 | **-1.41%** |
| Mean PSNR | 21.7042 dB | 21.8530 dB | **+0.1488 dB** |
| Pixel-weighted PSNR | 21.0204 dB | 21.1750 dB | **+0.1546 dB** |
| Mean SSIM | 0.456895 | 0.478160 | **+0.021265 / +4.65%** |
| Pixel-weighted SSIM | 0.474235 | 0.496647 | **+0.022412 / +4.73%** |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Native training | 1,293.68 s | 1,297.10 s | +3.42 s / +0.26% |
| Wall time | 1,312.76 s | 1,315.86 s | +3.11 s / +0.24% |
| Periodic checkpoints | 222.41 s | 225.68 s | +3.27 s |
| Topology refinement | 113.31 s | 115.81 s | +2.50 s |

| Held-out view | Control PSNR | AbsGrad PSNR | Delta | Control SSIM | AbsGrad SSIM | Delta |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 23.5225 | **23.7709** | +0.2484 | 0.54899 | **0.56805** | +0.01906 |
| 1 | 19.6520 | **19.7824** | +0.1304 | 0.34879 | **0.37452** | +0.02573 |
| 2 | **23.9524** | 23.8835 | -0.0689 | 0.54729 | **0.56578** | +0.01849 |
| 3 | 19.0799 | **19.2984** | +0.2185 | 0.44416 | **0.47114** | +0.02699 |
| 4 | 22.3140 | **22.5298** | +0.2158 | 0.39525 | **0.41131** | +0.01606 |

The candidate passes the provisional non-regression envelope of no more than
0.10 dB PSNR and 0.002 SSIM below its same-binary control; it improves both
aggregate metrics instead. Visual inspection of the two weakest/different
held-out crops shows no new hole, black region, seam or loss of structure. The
candidate is slightly more coherent on fine vegetation. The broad tonal and
texture differences from the real photograph remain a model/product issue for
the later multi-cell and raster gates.

The dev.57 AbsGrad replay completes 76.6% faster in wall time and 76.8% faster
in native training than the dev.49 AbsGrad run. Peak observed VRAM remained
about 8.3 GiB, with the RTX 3090 normally at 98-99% utilization during
training. This is a representative-cell trainer result, not a full-scene UAV
2026 timing comparison.

## Retained evidence

The candidate remains on BIGZEN under
`/home/olivier/benchmarks/aerial-gcp-hq-absgrad050-20260814/cell0`.
The controlled reference remains under
`/home/olivier/droneai-hq-v4-central44-checkpoints-r3-20260814/central44-hq-v4-6m-multicell-r3/cell_0`.

The clean current-main evidence remains under:

- AbsGrad 0.50:
  `/home/olivier/benchmarks/aerial-gcp-hq-absgrad050-main6c18c9e-20260814/cell0`;
- same-binary control:
  `/home/olivier/benchmarks/aerial-gcp-hq-reference-main6c18c9e-20260814/cell0`.

| Candidate artifact | Size | SHA-256 |
| --- | ---: | --- |
| `point_cloud.ply` | 1,509,602,008 B | `07bd1f799140e390866d73849f869486eb3010def7e42b43563e0f085e2d4402` |
| `trainer_run.json` | 6,817 B | `ee174aa3e3b6dc2adf15b900232249955bd81c4e1eb0c448f99189eee35eea78` |
| `evaluation/metrics.csv` | — | `e528719f0974884dfff79edb434b147a12f0c621318a749c6b10e5e12517486b` |

| Current-main artifact | AbsGrad 0.50 SHA-256 | Same-binary control SHA-256 |
| --- | --- | --- |
| `point_cloud.ply` | `ce0b631833566854546f25db8fc68e0570b374b4e2902170380e127cd87bf44a` | `3035673c0ddabd7f6dd0b7afde89762b8da1a79149c1fb9eefcd924926c5a265` |
| `training.ckpt` | `c1e9b246d181c013b73667ee2d6bf544a99a69fc49cc2343c723b7292e8bae66` | `0b8b3318abf0d498e5002aaefbf1935e3f89b18d8d8f7d60448aae73c36264d4` |
| `trainer_run.json` | `45e7748135a96cf358dc85b76b20f5e4ce6d27b53d2ea92cdfecba9fdc0407e6` | `f7849e694d46d12397cb3c7155e92fe48cbc948c0a9f956a12d4b8e7462c3599` |
| `evaluation/metrics.csv` | `aba1deaabde30ae79a316226b7ffa7a6470cb6b934009b8a23b7bea726a6b90a` | `1a1db8b57ce1165aed0fbf4e2fd646fc4d83651c9b63a1ddbd0ad15bc0cae536` |

The 4.63 GB training checkpoint, prediction/target pairs and metrics are
retained with the run. This evidence must not be deleted until the current
trainer and full-scene follow-up have been qualified.

## Promotion gates

1. Exercise multiple cells that include vegetation, hard edges and façades.
2. Run the complete Normal and HQ core/buffer aggregation path, then validate
   density, coverage, RGB/height seams, CRS and GSD on the final products.
3. Compare a 40,000-iteration, like-for-like dataset run with the published
   RTX 3090 timings; the present 23-source-image cell is not directly comparable
   with the much larger UAV 2026 scenes.

The external timing reference is the UAV partitioning study published in
*Remote Sensing* 18(9), 1400:
<https://doi.org/10.3390/rs18091400>.
