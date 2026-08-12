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
  intrinsics and grouped train/test assignment. Individual subviews without
  any projected sparse-Gaussian support are discarded before scheduling;
  a dataset with no supported view still fails closed. Unsupported tile-mode
  values also fail closed.
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
profile can be enabled. The standalone runner now uses the same 2 cm HQ GSD as
production instead of inheriting the balanced runner's 5 cm preview default.

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
   implemented. The raster Job now publishes per-boundary RGB/height jump
   distributions and boundary-to-interior ratios as evidence-only metrics;
   qualification thresholds remain the representative-block gate.**

The first validation is a short representative native block. A full BIGZEN
E2E becomes useful only after that block passes memory, density, seam and
runtime gates.

Current representative-block evidence on BIGZEN:

- the selected central core covers 29,462 m², uses 59 native camera crops and
  starts from 22,547 sparse Gaussians;
- its 2 cm density plan requests about 5.7 M retained Gaussians, below the
  12 M resident hard cap and within the measured RTX 3090 memory envelope;
- a fixed 7% growth reference reached only 613,790 Gaussians at the final
  growth window (10.8% of target). The incomplete result was quarantined and
  is not a quality reference;
- resident HQ training now opts into capacity-targeted growth. At each
  200-iteration window it recomputes the compound growth needed to reach the
  planned retained population by iteration 14,800, compensates conservatively
  for pruning and candidate eligibility, and clamps the requested fraction to
  7–25%. Once that final growth window has run, adaptive HQ freezes the
  topology so later pruning cannot silently invalidate the planned density;
  the remaining iterations optimize the fixed population. Existing
  non-resident profiles retain the fixed 7% behavior and their configured
  pruning window. The policy, target and per-window fraction are recorded for
  reproducibility.
- the first complete 30,000-iteration reference run reached 5.7 M Gaussians,
  used about 10.1 GiB VRAM, and passed its 22-view canary at 22.98 dB PSNR and
  0.523 SSIM. Post-training scale and opacity filtering retained 5,643,241
  Gaussians (99.0%), 40,015 below the strict 5,683,256 requirement for 2 cm.
  The density gate correctly rejected the raster instead of silently
  advertising 2 cm. Resident adaptive planning now distinguishes the retained
  surface target from a pre-filter training target sized for 98% retention;
  this representative block therefore targets 5.8 M before filtering. The
  failed run and its checkpoint/model remain retained as qualification
  evidence.
- an interrupted retention-reserve rerun exposed a final-window edge case: if
  the target was reached at iteration 14,600, the iteration-14,800 refinement
  requested zero growth and then pruned 60,015 Gaussians immediately before
  topology freeze. The final adaptive window now always reserves the minimum
  split budget, allowing those freed slots to be recycled back to the exact
  capacity target. The interrupted run is retained under its distinct label
  and is not quality evidence.

The corrected `reference-absolute-retention98-finalrestore` run completed all
30,000 iterations in 6,310 seconds on the BIGZEN RTX 3090. The final topology
freeze restored exactly 5.8 M Gaussians after pruning, peak observed VRAM was
about 10.3 GiB, and filtering retained 5,739,213 Gaussians. The strict density
gate accepted that population against 5,683,256 required at 2 cm. The held-out
canary also passed at 22.94 dB PSNR and 0.524 SSIM.

The first raster attempt was nevertheless rejected by the independent spatial-coverage
gate. It rendered 10,978 x 13,331 pixels with 87.0% valid pixels and 93.4% of
the 16 x 16 cells above the 25% coverage threshold, but two right-hand corner
cells were empty and three additional border cells were below 1%. Therefore
`worst_cell_ratio` was 0.0 against the required 0.01. The filtered model,
checkpoint, canary, run manifest and coverage report remain retained under the
distinct run label. This is negative qualification evidence: training density
is now validated, but the HQ raster path is not accepted and the PR must not be
merged until the edge-coverage failure is explained and corrected. Diagnosis
confirmed that all five sub-threshold cells were footprint-boundary cells and
that the two empty cells were the right-hand corners; the worst interior cell
was 23.0%. `GAUSSIAN_MAP_COVERAGE_V2` therefore applies the strict localized-hole
minimum only to cells surrounded by the expected footprint. Boundary cells
remain protected by the aggregate covered-cell and camera-cell checks. A
raster-only BIGZEN replay from the retained model is the acceptance gate for
this correction.

The next gate is a raster-only replay from the retained model, followed by a
repeat coverage check. AbsGrad A/B runs start only after the reference raster
passes; they must not obscure this independent product-coverage defect.

## Phase 3 — Densification A/B

Status: neutral native candidates implemented; A/B execution remains pending
the Phase 2 reference result.

Run identical seeded block experiments for:

- reference absolute-gradient behavior;
- AbsGrad threshold 0.25;
- AbsGrad threshold 0.50;
- multi-view contour-guided densification.

`reference-absolute-absgrad025` and
`reference-absolute-absgrad050` keep every validated
`reference-absolute` learning rate and schedule unchanged and vary only the
AbsGrad contribution to MRNF growth ranking. They remain explicitly
experimental until this A/B gate passes; the older dev.36 profiles are not
used because they also change rotation and optimizer calibration. The local
runner accepts a bounded `--run-label`, isolating orthomosaics, checkpoints and
reports for seeded candidates that share the same immutable input block.

Compare retained population, PSNR/SSIM/LPIPS, edge MTF, floaters, DEM residuals,
peak VRAM and wall time. No default changes without a recorded win. This phase
is motivated by [AbsGS](https://arxiv.org/abs/2404.10484), which targets
gradient cancellation that can prevent large Gaussians from splitting.

The completed native manifests are compared with
`tools/compare_gaussian_qualification_runs.py`. It fails closed if the
dataset fingerprint, trainer binary, seed, iteration budget, density target or
any other controlled parameter differs, and accepts only the versioned
reference/AbsGrad parameter fields as experimental variables. Raster/DEM
residuals and edge evidence remain separate product-level gates because they
operate on the final GeoTIFFs, not on trainer telemetry.

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
