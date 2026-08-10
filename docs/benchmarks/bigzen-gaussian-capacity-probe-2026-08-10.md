# BIGZEN DroneGS Gaussian-capacity probe — 2026-08-10

## Verdict

The native DroneGS dev.47 trainer successfully allocated and executed one real
optimization step with 12.0 M, 13.7 M and 16.0 M Gaussian capacity on BIGZEN's
24 GiB RTX 3090. This qualifies the 12 M `high-quality-v2` operator ceiling as
an allocation-safe starting point on that GPU. It does **not** qualify a full
4K aerial workload or prove that increasing capacity improves the raster.

## Environment and method

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB |
| Idle GPU memory | 532 MiB used, 23,794 MiB free |
| Native image | `drone-colmap:63b4f8b86060ff0e4063c22606b2ebbf41af8315` |
| Trainer | DroneGS `0.5.0-dev.47` |
| Sparse fixture | 3 cameras, 400 planar points |
| Images | 3 existing Villesèque JPEGs, mounted read-only |
| Training input | SH3, FastGS, factor 8, maximum width 256 px |
| Exercise | one complete optimizer iteration per capacity |
| Sampling | `nvidia-smi` memory used approximately every 0.2 seconds |

The fixture forced construction of the real ordered-alpha training context and
its Gaussian, projection, gradient, optimizer and refinement buffers. It was
not a synthetic `cudaMalloc` loop. Source datasets were never modified.

## Results

| Requested capacity | Exit | Observed peak GPU memory |
|---:|---:|---:|
| 12,000,000 | 0 | 12,328 MiB |
| 13,700,000 | 0 | 15,102 MiB |
| 16,000,000 | 0 | 15,324 MiB |

The coarse sampler can miss a short peak and CUDA allocation behavior is not
strictly linear, so these numbers are bounds observed during this probe rather
than an exact byte-per-Gaussian calibration. The earlier full Villesèque run
used about 7.1 GiB at a 3 M cap because decoded 4K images, raster pairs and
other transient buffers materially increase the footprint.

Therefore:

- `normal-v2` may use its calculated Villesèque target of about 4.3 M;
- `high-quality-v2` remains capped at 12 M for the first real qualification;
- 13.7 M is the current conservative memory-model ceiling on a free 24 GiB
  card;
- the fact that 16 M allocated is not authority to use 16 M in production.

The next qualification gate is a full Villesèque `high-quality-v2` run at 12 M
with 4,096 px training images and 30,000 iterations. Record final Gaussian
count, peak VRAM/RAM, runtime, sharpness/ghosting metrics and a native-resolution
comparison against the Metashape orthomosaic.

## Cleanup

All probe inputs and outputs were deleted after the measurements. BIGZEN ended
with 94 GiB free on the 226 GiB Ubuntu filesystem; source benchmark datasets,
the K3s quality-gate pipeline and the active immutable service images remain.

