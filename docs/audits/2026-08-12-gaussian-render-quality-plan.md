# Gaussian render-quality implementation plan — 2026-08-12

This ledger turns the August render-quality audit into short, independently
verifiable delivery phases. Quality claims remain unchanged until the listed
qualification gates pass.

## Phase 1 — Correctness foundations

Status: implemented and component-qualified on BIGZEN (RTX 3090).

- Native training-image reduction uses an area filter instead of nearest or
  bilinear sampling. Fractional source-pixel coverage is tested.
- The native and Python/CuPy renderers share the same degree 0–3 spherical
  harmonic convention. A cross-language CTest compares representative
  directions and degrees.
- `tile_mode` values 1, 2 and 4 create real native-image crops with adjusted
  intrinsics and grouped train/test assignment. Unsupported values fail
  closed.
- The public configuration calls the implemented appearance extension
  `opacity-SH-v1`. DroneAI does not claim view-dependent FAGK scale or
  rotation.
- Adaptive Normal/HQ products persist their planned and retained Gaussian
  density between Stage Jobs. Rasterization fails closed when the retained
  population cannot support the requested GSD. Fast preview is deliberately
  exempt.

Qualification evidence:

- 8/8 native CTest gates passed, including CUDA rasterization, CUDA training,
  tiled training and native/Python SH parity.
- 57/57 focused Python workflow, capacity, artifact and CUDA-renderer tests
  passed.
- strict typing passed for 17 COLMAP-worker and 24 Gaussian modules.

## Phase 2 — Geographic HQ blocks

Status: in progress. Projected-ground core/buffer cells and deterministic
geographic core ownership, calibrated terrain-envelope camera footprints and
native JPEG crops are implemented and component-qualified. Area/GSD/VRAM-aware
resident-cap planning and cross-Job resident streaming are also implemented.
Seam qualification on a representative native block remains before the new HQ
profile can be enabled.

1. Define the partition in projected ground coordinates, independently of the
   reconstruction's local axes. **Implemented.**
2. Derive each camera's ground footprint from calibrated rays and an explicit
   terrain-height envelope. Assign cameras only when their footprint
   intersects a block buffer and their view is geometrically usable.
   **Implemented.**
3. Convert each block footprint back to source-image polygons, extract native
   crops with a configurable pixel margin, and retain crop-relative
   intrinsics. **Implemented with a 128 px contract margin; configuration is
   deferred until the representative-block gate shows a need.**
4. Train one resident core/buffer block at a time with a 12 M hard resident
   cap. Plan the number of blocks from area, requested GSD, target spacing and
   available VRAM; do not encode a universal 40 M constant. **The planner and
   automatic compact geographic grid are implemented. For the 209,400 m²
   reference case at 2 cm and 3.6 px spacing, it resolves about 40.4 M merged
   Gaussians, seven minimum resident cells with 20% buffer, and a 12 M hard
   per-cell cap. Training now persists each resident buffer model, records its
   uniquely owned core population, then releases its GPU allocation before the
   next cell; the old global GPU merge is forbidden. Filtering and
   rasterization reload one buffer at a time across Stage Jobs. Raster
   publication writes only the corresponding pixel-snapped core so overlap
   support remains available at seams without duplicate ownership.**
5. Merge only buffer-supported content into each core. Record overlap,
   per-core density and seam evidence in the stage artifacts. **Buffer/core
   ownership, portable per-cell counts, extents and model checks are
   implemented. Seam-image metrics remain the representative-block gate.**

The first validation is a short representative native block. A full BIGZEN
E2E becomes useful only after that block passes memory, density, seam and
runtime gates.

## Phase 3 — Densification A/B

Status: pending the Phase 2 representative block.

Run identical seeded block experiments for:

- reference absolute-gradient behavior;
- AbsGrad threshold 0.25;
- AbsGrad threshold 0.50;
- multi-view contour-guided densification.

Compare retained population, PSNR/SSIM/LPIPS, edge MTF, floaters, DEM residuals,
peak VRAM and wall time. No default changes without a recorded win. This phase
is motivated by [AbsGS](https://arxiv.org/abs/2404.10484), which targets
gradient cancellation that can prevent large Gaussians from splitting.

## Phase 4 — 2D Gaussian backend evaluation

Status: research spike after the densification gate.

Add 2DGS behind a backend interface, not as a replacement in the existing
trainer. Qualify planar-disk ray intersection, depth distortion and normal
consistency on the same block, following
[2D Gaussian Splatting](https://arxiv.org/abs/2403.17888). Compare buildings,
ground surfaces, DEM residuals, memory and throughput against the 3DGS
baseline.

## Phase 5 — Rasterizer optimization

Status: defer until profiling the improved model.

Profile first, then consider persistent spatial indexing, tighter
opacity-aware elliptical culling, cached transforms/SH colors, compact radix
keys, memory-pool reuse, mmap/chunked PLY input and partition streaming. If
depth-order artifacts remain, evaluate a hierarchical resort strategy inspired
by [StopThePop](https://arxiv.org/abs/2402.00525). Each optimization requires
image parity, bounded memory and timing evidence before adoption.
