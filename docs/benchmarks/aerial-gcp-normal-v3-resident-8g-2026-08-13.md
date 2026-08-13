# Aerial GCP Normal v3 resident 8 GiB qualification — 2026-08-13

## Verdict

The four-block `normal-v3` candidate completed its real 15,000-iteration
training, filtering, density gate and 2 cm GeoTIFF production on BIGZEN.  The
planner was constrained with `DRONEAI_GAUSSIAN_VRAM_BUDGET_GIB=8`; sampled
physical use peaked at 3,859 MiB, below that logical envelope.  All four
held-out canaries passed and the retained unique-core density supports the
requested GSD.

The first raster exposed hard core-boundary discontinuities.  Replacing hard
stitching with linear core/buffer feathering reduced the measured boundary
jumps by roughly one order of magnitude for colour and by one to two orders of
magnitude for height, while adding about ten seconds to post-processing.  The
feathered raster is therefore the qualified resident product contract.

This is a representative multi-block GPU qualification, not a claim of survey
accuracy and not evidence from a physical 8 GiB card.  The source
reconstruction is geographically aligned, but this run does not compare the
final raster with independent checkpoints.

## Reproducible scope

| Item | Value |
| --- | --- |
| Application code used for the feathered run | `5eab48c` |
| Execution host | BIGZEN, Ubuntu WSL2 on Windows 10 |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB |
| Logical planner envelope | 8 GiB |
| Container CUDA banner | 12.9.2 |
| CuPy | 14.1.1 |
| Trainer | `dronegs-native-mrnf-fastgs` 0.5.0-dev.48 |
| Trainer SHA-256 | `01eab7cd9da271a5ca5a3c6d7329aaa212789746baec8358498b943fef1cbdd2` |
| Runtime image | `drone-colmap:f16fe91` |
| Registered cameras | 33 |
| Filtered sparse seeds | 39,205 from 52,232 |
| Robust projected area | 88,239.71 m² |
| Product CRS and GSD | `EPSG:32636`, 0.020 m/px |

The run used the local `normal` alias, which resolves to the immutable
`normal-v3` recipe: width 2,400, 4,096 SIFT features, SH degree 3, data factor
4, 15,000 iterations per resident block, 3 M scene floor, 8 M operator cap and
an eight-pixel unique-core spacing target.

```bash
DRONEAI_GAUSSIAN_VRAM_BUDGET_GIB=8 \
DRONEGS_BIN=/candidate/dronegs \
python3 tools/run_local_gaussian.py \
  /qual/normal-subset-workspace-v2 \
  --profile normal \
  --run-label normal-v3-8g-central-multiblock \
  --verbose
```

## Capacity, canaries and runtime

The planner selected a 2 x 2 projected-ground grid.  It targeted 3.6 M
Gaussians before filtering and capped each resident buffer at 1.8 M, below the
conservative 2.3 M limit calculated for the 8 GiB envelope.

| Cell | Cameras | Final buffer Gaussians | PSNR | SSIM | Native wall time |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 31 | 1,793,769 | 23.9809 dB | 0.6683 | 617.10 s |
| 1 | 31 | 1,795,630 | 24.5724 dB | 0.6783 | 641.61 s |
| 2 | 30 | 1,800,000 | 24.1955 dB | 0.7079 | 617.66 s |
| 3 | 29 | 1,791,970 | 24.7922 dB | 0.7471 | 570.50 s |

The native wall-time sum was 2,446.87 s (40 min 46.9 s).  Native training
accounted for 2,413.53 s; loading, decoding, checkpoints and evaluation were a
small fraction of the total.  GPU telemetry sampled every five seconds over
the command recorded 482 samples, 80.3% mean utilization, 100% maximum and
3,859 MiB peak VRAM.  The result is therefore compute-bound in the trainer,
not PLY-I/O-bound.

Filtering retained 3,501,321 of 3,544,633 uniquely owned core Gaussians
(98.78%).  The density gate required 3,446,864 and accepted the result:

| Density metric | Result |
| --- | ---: |
| Achieved spacing | 0.15875 m |
| Achieved spacing | 7.9375 output pixels |
| Minimum compatible GSD | 0.019844 m/px |
| Requested GSD | 0.020000 m/px |

## Core/buffer A/B

The same trained and filtered models were rasterized twice.  The baseline
wrote each pixel from exactly one core.  The qualified version renders the
overlapping buffers on the common pixel grid and cross-fades their RGB and
height samples with a unit core and linear margins.

| Seam | Hard RGB mean / p95 | Feathered RGB mean / p95 | Hard height mean / p95 | Feathered height mean / p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 25.40 / 75.00 | 1.84 / 5.33 | 0.830 / 5.502 m | 0.022 / 0.072 m |
| 2 | 21.38 / 65.33 | 1.54 / 4.67 | 0.925 / 2.745 m | 0.036 / 0.132 m |
| 3 | 23.05 / 58.33 | 1.67 / 5.00 | 2.608 / 9.244 m | 0.059 / 0.239 m |
| 4 | 38.77 / 109.33 | 3.15 / 10.00 | 1.319 / 3.264 m | 0.067 / 0.228 m |

The boundary-to-neighbouring-gradient ratios fell from 9.70–11.38 to
1.00–1.06 for RGB and from 15.59–36.12 to 1.00–1.02 for height.  Coverage also
improved from 99.98341% valid pixels and a 99.72426% worst cell to 99.99997%
and 99.99756%.  The post-processing command increased from 75.65 s to 85.53 s;
this is 0.4% of the native training time.

## Raster and artifact contract

Both feathered products share a 19,742 x 12,248 grid, 0.020 m affine pixel
size, `EPSG:32636` CRS and projected extent
`[413977.6087, 6634271.6902, 414372.4487, 6634516.6502]`.

| Artifact | Bytes |
| --- | ---: |
| RGB GeoTIFF | 729,784,766 |
| Height GeoTIFF | 804,343,747 |

The output contract is `cupy-ortho-v4-resident-feather` with resident
compositing `linear-core-buffer-v1`.  The production Stage Job contract
publishes the four filtered PLYs as an indexed resident model set instead of
requiring a global GPU merge.

The run also exercised the bounded binary-PLY path on a separate real
10,318,799-Gaussian, 2,435,238,043-byte model.  Memory-mapped load took 6.113 s,
atomic save 13.711 s and reload 5.917 s; sampled digests and complete output
bytes matched exactly.  These exact I/O improvements reduce host-memory and
failure risk but, as the trainer timings show, cannot materially shorten the
compute-bound optimization loop.

## Defects found and fixed

The qualification caught two integration defects before promotion:

- a reloaded resident PLY defaulted to colour SH degree zero even though its
  stored colour and opacity coefficients were degree three;
- floating-point `ceil` could add a phantom row or column when a resident
  extent was reconstructed from exact global pixel indices.

Both cases now have CPU/GPU regression coverage.  The relevant BIGZEN suite
passes 69 tests on CUDA 12.9.2.

## Qualification boundary

Accepted:

- real four-block training and replay under an 8 GiB planning envelope;
- all canary, retention, density and coverage gates;
- bounded physical VRAM below 8 GiB;
- exact resident raster dimensions and feathered seam continuity;
- reproducible, bounded PLY persistence and resident model-set publication.

Still separate:

- a run on a physical 8 GiB device under concurrent production load;
- final survey accuracy against independent checkpoints;
- complete five-Job Kubernetes publication on OVHcloud;
- the distinct 30,000-iteration HQ and facade qualifications.

All workspaces, hard-stitch baselines, feathered products, reports, telemetry
and PLY benchmark artifacts remain on BIGZEN.  No cleanup was performed.
