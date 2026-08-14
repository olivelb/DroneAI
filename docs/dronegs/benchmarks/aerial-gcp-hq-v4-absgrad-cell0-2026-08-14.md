# Aerial GCP HQ v4 AbsGrad cell-0 qualification — 2026-08-14

## Verdict

AbsGrad with a score threshold of `0.50` improves the five held-out views of
the representative aerial cell while keeping the same 5.1 M resident Gaussian
population and effectively the same runtime. Relative to the controlled
absolute-gradient reference, mean PSNR increases by 0.224 dB and mean SSIM by
0.0209. Every held-out view improves on both metrics.

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

## Controlled scope

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

## Quality and runtime

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

## Retained evidence

The candidate remains on BIGZEN under
`/home/olivier/benchmarks/aerial-gcp-hq-absgrad050-20260814/cell0`.
The controlled reference remains under
`/home/olivier/droneai-hq-v4-central44-checkpoints-r3-20260814/central44-hq-v4-6m-multicell-r3/cell_0`.

| Candidate artifact | Size | SHA-256 |
| --- | ---: | --- |
| `point_cloud.ply` | 1,509,602,008 B | `07bd1f799140e390866d73849f869486eb3010def7e42b43563e0f085e2d4402` |
| `trainer_run.json` | 6,817 B | `ee174aa3e3b6dc2adf15b900232249955bd81c4e1eb0c448f99189eee35eea78` |
| `evaluation/metrics.csv` | — | `e528719f0974884dfff79edb434b147a12f0c621318a749c6b10e5e12517486b` |

The 4.63 GB training checkpoint, prediction/target pairs and metrics are
retained with the run. This evidence must not be deleted until the current
trainer and full-scene follow-up have been qualified.

## Promotion gates

1. Re-run this exact cell with the current optimized trainer and compare both
   quality and elapsed time against this dev.49 baseline.
2. Exercise multiple cells that include vegetation, hard edges and façades.
3. Run the complete Normal and HQ core/buffer aggregation path, then validate
   density, coverage, RGB/height seams, CRS and GSD on the final products.
4. Compare a 40,000-iteration, like-for-like dataset run with the published
   RTX 3090 timings; the present 23-source-image cell is not directly comparable
   with the much larger UAV 2026 scenes.

The external timing reference is the UAV partitioning study published in
*Remote Sensing* 18(9), 1400:
<https://doi.org/10.3390/rs18091400>.
