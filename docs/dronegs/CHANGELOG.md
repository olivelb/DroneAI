# DroneGS changelog

This changelog covers the standalone Gaussian trainer project.

## 0.5.0-dev.32 - Extended live SH color

- Match pinned LichtFeld FastGS by clamping per-splat SH color to `[0,4]`
  instead of `[0,1]`.
- Keep DC and active higher-order SH gradients live over the same extended
  interval in both the CPU oracle and CUDA backward path.
- Add explicit CPU ceiling/live-gradient checks and exercise extended color in
  CUDA forward/backward parity.
- Mark the adapted CPU rasterization translation unit GPL-3.0-or-later and
  extend the exact-source provenance record.

## 0.5.0-dev.31 - Local-density MRNF initialization

- Replace the single scene-wide Gaussian scale with the MRNF two-nearest-
  neighbour scale formula adapted from pinned LichtFeld.
- Add an independent deterministic balanced KD tree and parallel exact queries
  without adding a runtime library dependency.
- Bound local scales by central-75% scene extents and preserve LichtFeld's
  `1e-3` scale floor and fewer-than-three-points fallback.
- Mark the adapted initialization translation unit GPL-3.0-or-later and extend
  the provenance register with its exact source revision.
- Add compact/diffuse-neighbourhood, duplicate-point, isotropy, and fallback
  tests while retaining all six CPU, CUDA, training, and LPIPS-tool suites.

## 0.5.0-dev.30 - Portable recent-NVIDIA CUDA builds

- Remove the Ada-only CUB Policy610 override and `--maxrregcount=64` compiler
  ceiling while retaining dev.29's generic shared-memory backward batching.
- Default local builds to CMake's `native` CUDA architecture detection.
- Add a `portable` preset with CUDA 12.8 real cubins for Turing, Ampere,
  Ada, Hopper, and Blackwell (`75`, `80`, `86`, `87`, `89`, `90`, `100`,
  `101`, and `120`).
- Keep explicit user-provided CMake architecture lists supported and give
  direct `CMAKE_CUDA_ARCHITECTURES` settings precedence.
- Return both stable key/value sorts to CUB's public default dispatch so nvcc
  and CUB select a valid policy for each compiled target.

## 0.5.0-dev.29 - Phase 4 shared-memory backward batching

- Cooperatively load projected splats into tile-local shared memory during
  both front-to-back transmittance recomputation and reverse gradient
  traversal.
- Share the recovered source index alongside each 48-byte projected record,
  removing up to 256 redundant global record/key reads per tile contribution.
- Preserve the exact per-pixel blend order, reverse gradient order, stable
  depth/source key, public outputs, and MRNF lifecycle.
- Pass all six CPU, CUDA, gradient, training, and LPIPS-tool suites.
- Reduce bounded Savères training/wall time to 39.50/43.97 seconds and
  Albagnac to 50.44/56.21 seconds. This is about 30% and 33% faster in wall
  time than the dev.26 PTX reference.
- Preserve Savères and Albagnac topology exactly; bounded PSNR, SSIM, and
  exact-pair LPIPS remain within numerical noise of dev.26/dev.28.
- Keep DroneGS opt-in. Speed parity is reached on these bounded three-scene
  controls, but convergence-length LichtFeld, visual, orthomosaic, and
  downstream detection gates remain open.

## 0.5.0-dev.28 - Phase 4 native Ada radix and occupancy tuning

- Keep dev.27's deterministic 64-bit depth/source key and compact 48-byte
  projected record after rejecting a 32-bit-key/52-byte-record alternative on
  Savères.
- Override CUDA 12.8's slower native Ada radix selection with stable CUB
  Policy610 kernels for both projected-depth and tile/depth pair sorts.
- Cap CUDA registers at 64 only when architecture 89 is compiled. Reject 96
  registers as neutral and 48 registers because spilling regresses throughput.
- Preserve all six CPU, CUDA, training, and LPIPS-tool suites and the existing
  equal-depth stability contract.
- Improve bounded Savères training/wall time from 58.25/62.77 seconds in
  dev.27 to 55.77/61.56 seconds. Improve Albagnac from 82.99/88.96 to
  79.87/87.52 seconds, with identical final topology and negligible
  PSNR/SSIM deltas.
- Beat the dev.26 PTX baseline in Savères wall time, while Albagnac remains
  4.3% slower in wall time. Keep the broader Phase 4 speed-parity gate open.
- Use only existing read-only COLMAP dense outputs; do not rerun COLMAP or the
  combined approximately 2,000-photo Albagnac workload.

## 0.5.0-dev.27 - Phase 4 native sm_89 compact-record sort

- Establish `89-real;89-virtual` before CMake enables CUDA so a clean default
  build contains an actual `sm_89` cubin plus compute_89 PTX.
- Replace the 144-byte projected CUB value with a 48-byte render record;
  preserve the existing depth/source key and keep the 16 SH bases in a
  separate source-indexed buffer.
- Reconstruct tile bounds from the sorted projected center/radius in two
  coalesced kernels. Avoid a full-record gather and reduce persistent
  projected-depth capacity by 112 bytes per reserved Gaussian.
- Pass all six CPU, CUDA, training, and LPIPS-tool suites; `cuobjdump` confirms
  `dronegs.1.sm_89.cubin`.
- Re-run bounded MRNF/progressive-SH validation on GAJAN, Savères, and
  Albagnac using existing read-only COLMAP dense outputs. No reconstruction,
  bundle adjustment, or combined 2,000-photo throughput run is performed.
- Preserve large-scene topology and PLY byte size exactly. PSNR/SSIM/LPIPS
  deltas stay below 0.00005 dB / 0.000004 / 0.00007 on Savères and Albagnac.
- Improve GAJAN training/wall time by 5.8%/15.5% versus the dev.26 PTX-JIT
  binary. Savères is wall-neutral (+0.4%); Albagnac remains +6.0% wall and
  needs kernel-level profiling before any broad speed claim.
- Keep the native trainer opt-in and the Phase 4 production gate open.

## 0.5.0-dev.26 - Phase 4 three-scene MRNF/SH/LPIPS validation

- Validate the dev.25 trainer without rerunning COLMAP on the existing GAJAN
  smoke, Savères, and Albagnac reconstructions: 25, 1,065, and 1,376 images
  with 9,324, 642,161, and 1,025,093 initial Gaussians respectively.
- Complete 1,200 bounded iterations on GAJAN and 220 bounded iterations on
  both large scenes. All runs reach progressive SH degree 3 and exercise the
  complete MRNF prune/reuse/noise/decay/compaction path without CUDA OOM.
- Measure final held-out PSNR/SSIM of 14.13256/0.205984 on GAJAN,
  15.72116/0.110614 on Savères, and 16.86978/0.240971 on Albagnac.
- Compute exact-pair AlexNet LPIPS v0.1 means of 1.046475, 1.201670, and
  1.085614 on 5, 17, and 22 held-out views respectively.
- Record wall times of 16.79 s, 62.49 s, and 83.94 s for the bounded runs.
  These establish execution and scaling evidence, not converged quality,
  full-dataset throughput, or LichtFeld parity.
- Audit the actual CUDA image in the validated binary: the pre-existing CMake
  cache produced sm_52 plus PTX, not the intended native sm_89 image. CUDA
  12.8/CUB native sm_86 and sm_89 device links currently fail because
  `DeviceRadixSort` Policy900 emits 50.5--51.5 KiB of static shared data over
  the 48 KiB linker limit. Record native Ada radix-sort repair as an open gate.
- Preserve owner and permissions during atomic LPIPS result/manifest writes;
  new evaluator files use mode `0644` and inherit the evaluation directory
  owner even when the container runs as root.
- Correct the documented LPIPS CLI invocation and add a persistent named
  Docker volume for the separately tracked AlexNet weight cache.
- Keep the Phase 4 production gate open. No full Albagnac throughput run,
  COLMAP bundle adjustment, or LichtFeld oracle replay is part of this phase.

## 0.5.0-dev.25 - Phase 4 complete MRNF lifecycle

- Continue refinement every 200 steps through iteration 28,500 while stopping
  growth at 15,000; later windows remain useful for prune/decay/compaction.
- Prune raw opacity below `logit(1/255)`, scale below `1e-10`, non-finite
  parameters, excessive-scale outliers, and robust spatial outliers.
- Hard-compact survivors into a dense device prefix while preserving all DC,
  SH, opacity, position, scale, and rotation Adam moments.
- Split selected parents after compaction, report reused versus newly appended
  slots, and retain the existing deterministic weighted-Gumbel/edge guidance.
- Inject deterministic opacity-weighted Gaussian means noise with the pinned
  MRNF exponent 150 and weight 50, bounded by each Gaussian's median scale.
- Apply refinement-time opacity decay 0.004 and scale decay 0.002, attenuated
  by remaining training fraction.
- Extend progress events and run manifests with prune/reuse/compaction counts
  and all lifecycle constants.
- Add synthetic tests for prune/compact/reuse and seed-stable noise.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.24 - Phase 4 progressive spherical harmonics

- Extend every Gaussian with the standard 45 channel-major non-DC values used
  by degree-3 3DGS PLY files; exports now contain learned values, not zeros.
- Add matching real SH bases for degrees 0–3 to the CPU oracle and CUDA
  rasterizer, with view direction computed from the camera center.
- Back-propagate active non-DC color coefficients on CPU/CUDA and update them
  with persistent Adam moments at one twentieth of the active DC rate.
- Start at degree zero and activate one band every
  `--sh-degree-interval` steps (default 1,000), capped by `--sh-degree`;
  emit schedule events and record the configured/final degree in the manifest.
- Copy SH values during splits and reset parent/child SH Adam moments.
- Add finite-difference coefficient tests, CPU/CUDA degree-3 parity, schedule
  tests, CLI validation, and real non-DC PLY serialization.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.23 - Phase 4 exact-pair LPIPS evaluation

- Persist exact lossless RGB8 held-out targets beside final PPM predictions;
  filenames are identical and pairing fails closed on missing or extra views.
- Add a separate, reproducible LPIPS v0.1 evaluator using the official
  `lpips` package with AlexNet and the required `[-1, 1]` input range.
- Write per-view `evaluation/lpips.csv`, aggregate
  `evaluation/lpips.json`, and atomically enrich run-manifest-v1 with the
  score, network, evaluator version, view count, hashes, and artifact sizes.
- Isolate PyTorch, torchvision, LPIPS, Pillow, and model-weight acquisition in
  `Dockerfile.lpips`; the native CUDA trainer and its hot path are unchanged.
- Add dependency-free unit coverage for exact pairing, aggregation, percentile
  calculation, atomic manifest enrichment, and mismatch rejection.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.22 - Phase 4 two-scene DC validation

- Prepared the independent Savères Mavic 3E RTK scene from 1,066 source
  photographs. COLMAP registered and undistorted 1,065 images (99.91%) with
  642,161 sparse points, 1.265 px mean reprojection error, and 0.078 m median
  Euclidean GPS residual.
- Replayed dev16, DC=0.010, and DC=0.020 for 500 and 1,000 steps with the exact
  same dev.21 binary (`96b9edc6...f146df4`) and dataset fingerprint.
- At 1,000 steps, Savères dev16 reaches 16.65243 dB / 0.131453 SSIM.
  DC=0.010 reaches 16.83870 dB / 0.131405, improving PSNR by +0.18628 dB but
  remaining effectively neutral in SSIM (-0.000048).
- DC=0.020 reaches 16.79856 dB / 0.132098, improving the Savères control by
  +0.14613 dB / +0.000644 SSIM and winning on 132/134 PSNR and 103/134 SSIM
  views.
- Across the 306 held-out Albagnac and Savères views, DC=0.020 improves the
  same-binary controls by +0.13101 dB / +0.001065 SSIM and wins on 303/306
  PSNR and 264/306 SSIM views. DC=0.010 gives the larger mean PSNR gain
  (+0.16354 dB) but lower SSIM coverage (204/306 views).
- The quality gain is not a speed gain. On Savères, DC=0.010 and DC=0.020
  increase 1,000-step manifest wall time by 14.6% and 19.3% versus dev16;
  Albagnac increases were 6.8% and 8.2%.
- Keep `dronegs-dev16` as the default throughput profile. Promote
  `calibrated-dc-0.020-opacity` to the recommended quality profile, without
  making it the global default before LPIPS and a larger-scene throughput gate.
- Dev.22 changes validation evidence, recommendation, version identifiers, and
  documentation only; the dev.21 optimizer and GPL-covered CUDA behavior are
  unchanged.
- Rebuilt the Release/sm_89 binary and passed all five native CPU/CUDA test
  executables.

## 0.5.0-dev.21 - Phase 4 intermediate-DC calibration

- Added `calibrated-dc-0.005-opacity`, `calibrated-dc-0.010-opacity`, and
  `calibrated-dc-0.020-opacity`; each changes only DC learning rate and epsilon
  plus the already isolated LichtFeld opacity family.
- Extended CLI validation, schedule events, manifest fields, native CUDA
  schedule tests, version identifiers, and GPL provenance.
- Passed all five native CPU/CUDA test executables.
- Replayed the dev16 control, both dev.20 endpoints, and all three intermediate
  rates for 500 steps with the exact same binary on the 1,376-image Albagnac
  split.
- At 500 steps, DC=0.010 gives the best mean PSNR gain (+0.18180 dB), while
  DC=0.020 gives the best mean SSIM gain (+0.001563) and improves SSIM on
  154/172 held-out views.
- Validated dev16, DC=0.010, and DC=0.020 for 1,000 steps with the same binary.
  DC=0.010 reaches 17.66035 dB / 0.252962 SSIM, improving the control by
  +0.14583 dB / +0.001328 and winning on 168/172 PSNR and 143/172 SSIM views.
- DC=0.020 reaches 17.63374 dB / 0.253027 SSIM, improving the control by
  +0.11923 dB / +0.001393 and winning on 171/172 PSNR and 161/172 SSIM views.
- Retain DC=0.010 as the primary balanced-quality candidate and DC=0.020 as the
  robust-view candidate. Keep dev16 as the default pending a second-scene
  replication and LPIPS.

## 0.5.0-dev.20 - Phase 4 DC-plus-opacity combination

- Added `lichtfeld-dc-opacity`, combining the two promising dev.19 families
  while retaining dev16 position, scale, rotation, and their epsilons.
- Extended CLI, manifest, JSON schedule events, version identifiers, direct
  CUDA schedule tests, and GPL provenance.
- Passed all five native CPU/CUDA test executables.
- The 500-step gate reaches 17.20034 dB / 0.245740 SSIM, approximately
  +0.1284 dB / +0.000239 versus the dev16 quality anchor.
- Replayed dev16 control, opacity-only, and DC-plus-opacity for 1,000 steps
  with the exact same final binary and five topology refinements.
- At 1,000 steps, opacity-only improves the control by +0.00706 dB /
  +0.000257 SSIM and wins on 109/172 PSNR and 130/172 SSIM views.
- DC-plus-opacity improves the control by +0.11791 dB / +0.000150 SSIM and
  wins on 142/172 PSNR views, but SSIM regresses on 106/172 views.
- Keep dev16 as the default. Retain opacity-only as the most homogeneous
  candidate and DC-plus-opacity as the best-PSNR candidate.
- The next calibration should sweep intermediate DC rates with LichtFeld
  opacity rather than selecting either endpoint.

## 0.5.0-dev.19 - Phase 4 MRNF one-family optimizer ablations

- Added five explicit profiles that replace exactly one dev16 parameter family
  with the pinned LichtFeld rate, schedule, normalization, and Adam epsilon.
- Refactored the optimizer to use an independent epsilon for DC, opacity,
  position, scale, and rotation; mixed profiles no longer leak epsilon changes
  into control families.
- Extended CLI, schedule JSON, manifest v1, CUDA tests, version identifiers,
  and GPL provenance for the family-isolated experiment.
- Replayed a same-binary dev16 control and all five 500-step ablations on the
  1,376-image Albagnac split.
- Position-only loses 1.12720 dB and 0.029407 SSIM versus the control,
  regressing 170/172 PSNR views and every SSIM view. Reject it.
- DC-only gains 0.08181 dB on 110/172 views but loses 0.000269 SSIM on
  average; retain it as a tradeoff candidate, not the default.
- Opacity-only gains 0.01622 dB and 0.000283 SSIM, improving 137/172 PSNR
  views and 142/172 SSIM views. It is the first no-compromise optimizer
  candidate, pending a longer-budget confirmation.
- Scale-only and rotation-only are effectively neutral at 500 steps.
- Keep `dronegs-dev16` as the default. The next optimizer experiment should
  combine DC and opacity without changing position, then validate at a longer
  iteration budget.

## 0.5.0-dev.18 - Phase 4 MRNF effective-update calibration

- Kept both optimizer configurations in one binary as
  `dronegs-dev16` and `lichtfeld-absolute`; restored the accepted dev.16
  quality profile as the CLI and training-context default.
- Added deterministic sampled optimizer telemetry at step 1, every training
  fifth, and the final step for DC, opacity, position, scale, and rotation.
- Telemetry reports incoming gradient RMS, actual post-clamp/post-normalization
  update RMS, resulting parameter RMS, and component sample count.
- Added the selected profile and its exact schedule constants to CLI parsing,
  JSON schedule events, manifest v1, and direct CPU/CUDA tests.
- Replayed both profiles for 500 steps on the same 1,376-image Albagnac split
  with the exact same dev.18 binary.
- `dronegs-dev16` reproduces the quality anchor at 17.07045 dB / 0.245493
  SSIM; `lichtfeld-absolute` reaches 16.11581 dB / 0.219508 SSIM.
- At step 1, gradients are identical but dev16 applies 20.35x larger DC and
  58.24x larger position updates, while its opacity update is 0.677x as large.
  The discrepancy persists throughout the run.
- Reject a single global LR correction. Dev.19 should run one-family
  ablations, beginning with DC and position, before changing opacity, scale,
  or rotation.

## 0.5.0-dev.17 - Phase 4 MRNF optimizer schedule isolation

- Replaced the dev.16 optimizer constants with the pinned LichtFeld MRNF
  profile for DC, opacity, scale, rotation, position, and Adam epsilon.
- Added the MRNF 80% spatial bound: the median of the three initial Gaussian
  axis widths between the 10th and 90th percentiles.
- Added exponential position `2e-5 -> 2e-7` and scale `0.007 -> 0.005`
  schedules using the optimizer step and total iteration count.
- Added a public read-only learning-rate diagnostic plus initial/final JSON
  events and exact manifest fields.
- Added direct CUDA tests for the 80% bound, all five initial learning rates,
  epsilon `1e-15`, first-step behavior, and exponential decay.
- Quantified the Albagnac position LR change from `0.00832225` in dev.16 to
  `0.000135236` in dev.17, a 61.5x reduction; DC falls 25x.
- The 500-step run ends at 1,173,577 Gaussians but regresses dev.16 by
  0.95662 dB and 0.026034 SSIM; 169/172 PSNR views and all SSIM views regress.
- Reject direct absolute-LR copying as a quality solution. The result proves
  that DroneGS gradient/parameter scale must be calibrated before using
  LichtFeld's optimizer values.
- Extended the GPL provenance entry to the inspected LichtFeld optimizer,
  schedule, bounds, and Adam sources.

## 0.5.0-dev.16 - Phase 4 reproducible Gumbel and edge guidance

- Replaced deterministic descending-score growth selection with weighted
  Gumbel top-K over the existing MRNF refinement score.
- Made selection reproducible by deriving each refinement seed from the CLI
  seed and iteration, then each per-Gaussian variate with SplitMix64.
- Added a luminance Sobel map on each already-scheduled training view and
  accumulated edge-weighted alpha contribution in the existing backward pass.
- Normalized positive per-Gaussian edge scores by their refinement-window
  median and applied LichtFeld's `1 + 0.25 * normalized_edge` guidance factor.
- Avoided LichtFeld's extra Canny plus full-raster passes over at least 8% of
  the dataset at every refinement; dev.16 performs zero extra edge renders.
- Added CUDA coverage proving same-seed bit-identical growth, different-seed
  selection, and retained split/capacity/statistic correctness.
- On Albagnac, dev.16 ends at 1,173,573 Gaussians, three below dev.15, and
  improves held-out quality by 0.01195 dB and 0.000550 SSIM.
- Trainer compute rises from 55.865 to 60.683 seconds (+8.6%); the small
  quality gain does not yet justify accepting the overhead as final.
- Extended the GPL provenance entry to the inspected LichtFeld Gumbel and edge
  rasterizer sources; the same two combined CUDA units remain GPL-covered.

## 0.5.0-dev.15 - Phase 4 MRNF growth isolation

- Added capacity-aware persistent Gaussian, gradient, statistic, and Adam
  buffers up to `--max-cap`.
- Added SSIM-error-map normalization and per-Gaussian contribution-weighted
  refinement statistics accumulated over 200-step windows.
- Added deterministic score/index selection at threshold `0.003`, 7% growth,
  and a rotated longest-axis parent/child split with reset optimizer moments.
- Added topology events with candidate/addition counts and manifest fields for
  protocol, refinement count, total additions, and final population.
- Added a forced 1-to-2 CUDA split test covering geometry, copied attributes,
  opacity, capacity, and statistic reset; all five native executables pass.
- The Albagnac 500-step run grows by 148,483 to 1,173,576 Gaussians, only 36
  above pinned LichtFeld's final population.
- Held-out quality regresses dev.14 by 0.0556 dB and 0.001321 SSIM while
  trainer compute rises 36.1%; population parity alone is rejected as a
  quality solution.
- Conservatively relicensed `cuda/rasterization.cu` and `cuda/trainer.cu`
  under GPL-3.0-or-later because dev.15 adapts pinned LichtFeld MRNF behavior;
  exact upstream/local paths are recorded in `GPL_COMPONENTS.md`.

## 0.5.0-dev.14 - Phase 4 analytical DSSIM objective

- Replaced the ordered trainer's pure active-pixel L1 objective with
  `0.8 * L1 + 0.2 * (1 - SSIM)`, retaining the dev.13 split, topology,
  rasterizer, schedules, and targets.
- Reused the separable 11x11 CUDA SSIM forward and added an analytical
  atomics-free image gradient that gathers at most 121 valid window centers
  per input sample.
- Added a public diagnostic output plus a direct CPU objective oracle and
  eight central finite-difference probes of the exact trainer gradient.
- Recorded the loss formula and `lambda_dssim=0.2` in manifest v1 and kept
  the independently implemented MIT provenance explicit.
- All five native test executables pass on the RTX 4070 Laptop.
- On the identical 1,376-image Albagnac split and 500-step schedule, mean
  held-out SSIM rises from 0.241900 to 0.246278 while PSNR changes from
  17.121187 to 17.115355 dB.
- DSSIM improves SSIM on 171 of 172 views, costs 4.4% more trainer compute,
  and does not close the topology/quality gap to pinned LichtFeld.

## 0.5.0-dev.13 - Phase 4 held-out quality gate

- Added an opt-in LichtFeld-compatible split where
  `scene_index % test_every == 0` is excluded from every Adam schedule.
- Added persistent CUDA PSNR and separable Gaussian 11x11 SSIM evaluation
  using LichtFeld's data range and valid-padding conventions.
- Added initial/final aggregate metrics, per-view CSV output, active-pixel
  coverage, evaluation timings, and optional lossless PPM predictions.
- Extended CLI and manifest v1 with the split, metric protocol, counts, and
  held-out artifacts while leaving legacy no-evaluation runs unchanged.
- Added deterministic split tests and a direct CPU oracle for GPU PSNR/SSIM.
- On Albagnac, reserved 172 of 1,376 views and improved held-out quality from
  14.0631 to 17.1212 dB and from 0.1811 to 0.2419 SSIM over 500 iterations.
- Ran the pinned GPL LichtFeld control on the identical split and settings:
  21.0686 dB, 0.6310 SSIM, and 1,173,540 final Gaussians.
- Kept the Phase 4 tag open: the gaps are 3.9474 dB and 0.3891 SSIM; LPIPS
  remains null because no local model/runtime is installed.

## 0.5.0-dev.12 - Phase 4 persistent geometry Adam

- Retained projected-conic and position/log-scale/quaternion gradients in the
  persistent CUDA context without per-step host readback.
- Added first/second Adam moments for all ten geometry parameters.
- Added a scene-diagonal-scaled position LR decaying from `1.6e-4` to
  `1.6e-6`, scale LR `0.005`, and rotation LR `0.001`.
- Bounded log-scales to the initialized global range plus/minus 4 and
  renormalized every quaternion after its update.
- Extended convergence coverage to require finite geometry, movement in all
  three parameter families, and unit quaternions.
- Reduced Albagnac anchor L1 from 0.200559 to 0.104295 over 500 iterations,
  versus 0.155307 with geometry fixed.
- Measured 39.552 seconds of trainer compute, 41.851 seconds wall, and an
  838 MiB sampled total-VRAM delta with no OOM.

## 0.5.0-dev.11 - Phase 4 anisotropic geometry backward

- Extended public backward with position, three log-scale, and normalized
  `w,x,y,z` quaternion gradients.
- Added a CPU finite-difference oracle and an analytical CUDA reverse chain
  through inverse covariance, spectral clamp, perspective, scale, and rotation.
- Added direct finite differences for all ten geometry components on a
  branch-stable anisotropic fixture.
- Measured 40.528 ms forward and 91.601 ms forward+geometry-backward medians
  at 1,025,093 Gaussians / 800x580.
- Completed an Albagnac 500-iteration regression in 32.233 seconds, reducing
  anchor L1 from 0.200559 to 0.155307 with no OOM.
- Kept persistent training on DC/opacity Adam; geometry integration is dev.12.

## 0.5.0-dev.10 - Phase 4 anisotropic covariance forward

- Replaced the projected scalar sigma with an inverse 2D conic and independent
  axis-aligned support radii.
- Added normalized quaternion rotation, non-uniform exponential scales, camera
  rotation, and the full perspective Jacobian to the CPU and CUDA projection.
- Added spectral clamping of both projected covariance eigenvalues to
  `[0.75², 8²]` pixels, preserving the previous footprint safety bounds.
- Routed the anisotropic conic through tiled forward rendering, reverse
  composition, persistent training, tile bounds, culling, and statistics.
- Added CPU rotation/swap, zero-quaternion rejection, extreme-scale clamp, and
  CUDA forward/backward parity tests with rotated cameras and conics.
- Measured 44.934 ms forward and 64.416 ms forward+backward medians at
  1,025,093 Gaussians / 800x580 across two order-balanced seven-run sets.
- Completed the real 1,376-image Albagnac 500-iteration run in 30.316 seconds,
  reducing anchor L1 from 0.200559 to 0.155307 with no OOM.
- Kept geometry fixed: position, scale, and rotation gradients are the next
  correctness sub-gate.

## 0.5.0-dev.9 - Phase 4 bounded JPEG decode experiments

- Replaced the single outstanding prefetch state with an ordered, bounded
  queue that supports multiple decoder workers without concurrent LRU mutation.
- Added concurrency, queue-capacity, refill, duplicate-decode, and CLI tests.
- Added optional `--prefetch-depth`, `--decode-workers`, and
  `--jpeg-idct-scale` controls and recorded them in the run manifest.
- Added an opt-in reduced-IDCT libjpeg path that decodes at the closest native
  1/2, 1/4, or 1/8 scale before any final resize.
- Benchmarked five short queue configurations and three 500-iteration
  Albagnac configurations.
- Rejected multi-worker decode as the default: depth 8 / two workers removed
  nearly all foreground wait but was 3.6% slower than the single-worker
  control because of CPU/GPU power contention on the laptop.
- Kept reduced IDCT opt-in: its 500-iteration wall time was 2.1% shorter than
  the same-cycle control, but its filtered target pixels changed anchor L1 and
  require held-out quality validation.
- Preserved the dev.8 defaults: one prefetch slot, one worker, full JPEG decode.

## 0.5.0-dev.8 - Phase 4 persistent ordered-alpha trainer

- Added an opaque persistent CUDA training context that retains Gaussians,
  projected records, CUB storage, tile pairs/ranges, image gradients, and Adam
  moments across iterations.
- Added grow-on-demand pair and tile capacities so repeated camera frames avoid
  per-iteration CUDA allocation after reaching their high-water marks.
- Connected RGB8 active-pixel L1, ordered-alpha backward, and DC/opacity Adam
  entirely on device; only pair-count and loss/active-pixel scalars return to
  the host during a step.
- Switched the experimental DroneGS binary to the ordered-alpha trainer and
  retained the additive trainer as a synthetic convergence control.
- Added side-by-side additive and ordered-alpha convergence coverage.
- Completed a real 1,376-image / 1,025,093-Gaussian Albagnac run at 500
  iterations in 25.80 seconds in Release/sm_89, reducing anchor L1 from
  0.200559 to 0.155306.
- Measured 14.35 seconds of trainer compute and a 651 MiB sampled peak
  total-VRAM delta; JPEG foreground wait is now a material 10.32-second cost.
- Renamed the manifest mode to
  `dronegs-fixed-topology-ordered-alpha-prototype`.

## 0.5.0-dev.7 - Phase 4 ordered-alpha backward

- Added public ordered-alpha backward outputs for DC color and opacity-logit
  gradients alongside the matching forward render.
- Added an original CPU reference that reverses each pixel's contributing
  sequence while carrying the composited tail color.
- Added a tiled CUDA backward kernel that reconstructs pre-splat
  transmittance, handles alpha/contribution clamps, and atomically accumulates
  per-Gaussian DC and opacity gradients.
- Added CPU and direct CUDA finite-difference checks plus CPU/CUDA parity for
  equal depths, multi-tile coverage, empty scenes, and early exit.
- Extended the opt-in raster benchmark with a forward+backward mode.
- Measured 52.889 ms combined median for forward+backward at 1,025,093 splats
  and 800x580 in Release/sm_89, versus 35.190 ms for forward alone.
- Kept the production trainer additive: the validated API still performs
  per-call allocation and host readback and is not yet a persistent training path.

## 0.5.0-dev.6 - Phase 4 GPU tile pipeline

- Moved visible-splat projection and tile-bound calculation from the host to CUDA.
- Added deterministic depth keys combining positive float depth bits with source
  index, followed by a stable CUB radix sort.
- Added a CUB exclusive scan for tile-pair offsets, GPU pair duplication, stable
  tile/depth sorting, and GPU tile-range construction.
- Removed host projected-splat vectors, per-tile vectors, and their transfers
  from the ordered-alpha forward path.
- Added equal-depth source-order and explicit multi-tile CPU/CUDA parity tests.
- Added an opt-in, reproducible end-to-end CUDA raster benchmark.
- Reduced the 1,025,093-splat / 800x580 benchmark median from 146.311 ms to
  35.395 ms across two order-balanced five-run sets on the RTX 4070 Laptop:
  4.13x faster and 75.81% less wall time.
- Kept the production trainer on the additive backward path; ordered-alpha
  backward, anisotropic covariance, and held-out quality parity remain open.

## 0.5.0-dev.5 - Phase 4 tiled-alpha CUDA forward

- Added a 16x16 tiled CUDA front-to-back alpha renderer.
- Added host reference binning into per-tile splat lists that preserve stable
  global depth order.
- Loaded splat batches cooperatively into shared memory and rendered one thread
  per pixel without RGB or transmittance atomics.
- Matched CPU RGB, residual transmittance, evaluated-pair, and
  contributing-pair outputs on multi-tile synthetic scenes.
- Added CUDA coverage for backgrounds, input-order independence, empty scenes,
  culling, contribution thresholds, and early-transmittance exit.
- Kept the trainer on the validated additive backward path; GPU binning,
  anisotropic covariance, and ordered-alpha backward remain open.

## 0.5.0-dev.4 - Phase 4 ordered-alpha oracle

- Added an original CPU reference for depth-sorted front-to-back alpha composition.
- Defined the raster camera, RGB, transmittance, and contribution-stat contracts.
- Matched the current projection and isotropic support rules while introducing
  bounded alpha, minimum-contribution, and early-transmittance thresholds.
- Added tests for single-splat contribution, depth order, background, culling,
  transmittance, and invalid cameras.
- Kept the CUDA training path additive until a tiled forward renderer matches
  the oracle and its backward pass is validated.
- Kept `dronegs-v0.5.0` untagged; this is a correctness foundation, not parity.

## 0.5.0-dev.3 - Phase 4 large-scene decode overlap

- Added a persistent, single-slot JPEG prefetch worker without concurrent LRU mutation.
- Precomputed the deterministic camera schedule and overlapped decode N+1 with render N.
- Split total JPEG service time from foreground image-wait time in run-manifest v1.
- Added prefetch started, consumed, and ready counters plus concurrency tests.
- Kept the 256 MiB resident LRU bound; one decoded image may additionally be in flight.
- Reduced median Albagnac image wait to 0.954 s and median 500-iteration wall
  time to 59.63 s at equivalent anchor loss.
- Improved warm end-to-end wall time by 15.9% versus a same-session dev.2
  control on 1,376 images and 1,025,093 Gaussians.
- Kept `dronegs-v0.5.0` untagged; ordered alpha compositing and quality parity remain open.

## 0.5.0-dev.2 - Phase 4 large-scene memory

- Changed decoded training targets and GPU transfers from float32 RGB to RGB8.
- Replaced eager all-image decoding with a lazy 256 MiB byte-bounded LRU cache.
- Added cache hit, miss, eviction, capacity, peak-residency, and decode timings.
- Added a 2,048-image cardinality stress test for the memory bound.
- Reduced the GAJAN-25 decoded target peak by 75% to 15.12 MB.
- Reduced the median 500-iteration training loop by 11.6% at equivalent anchor loss.
- Passed a real 1,376-image / 1,025,093-Gaussian run in 247.4 seconds with
  267.3 MB peak decoded residency and 309 cache evictions.
- Kept `dronegs-v0.5.0` untagged; this is a scaling sub-gate, not quality parity.

## 0.5.0-dev.1 - Phase 4 experimental

- Preserved COLMAP world-to-camera poses and added native JPEG decoding.
- Added PINHOLE and SIMPLE_PINHOLE projection with resized intrinsics.
- Added an original CUDA additive Gaussian rasterizer and analytical backward pass.
- Added Adam optimization for DC color and opacity with deterministic camera order.
- Added initial/final anchor loss and training time to run-manifest v1.
- Added an end-to-end GPU convergence test and a GAJAN 25-image smoke report.
- Kept positions, scales, rotations, topology, and non-DC SH coefficients fixed.
- Did not tag `dronegs-v0.5.0`: ordered alpha compositing and the fixed-topology
  quality parity exit gate are not complete.

## 0.4.0 - Phase 3

- Added the original MIT C++23/CUDA DroneGS project and development image.
- Added strict parsing of trainer CLI contract v1.
- Added bounded COLMAP binary camera, image, and sparse-point loading.
- Added fixed-topology Gaussian initialization and atomic binary PLY export.
- Added atomic run-manifest-v1 output with Git provenance.
- Added native COLMAP/CLI/PLY tests and a finite-difference CUDA gradient test.
- Verified the native PLY with DroneAI's existing CuPy `GaussianModel` on GPU.
- Added a containerized LichtFeld baseline suite and Docker-aware VRAM sampling.

## 0.3.0 - Phase 2

- Added a validated backend-neutral training request and normalized result.
- Added LichtFeld and contract-v1 DroneGS subprocess adapters.
- Kept LichtFeld as the default while adding explicit environment and mission selection.
- Wired the existing partitioned orthophoto workflow through the backend boundary.
- Documented the pinned LichtFeld CLI's lack of user-controlled seed support.

## 0.2.0 - Phase 1

- Added a versioned, backend-neutral benchmark suite format.
- Added isolated repeated runs with immutable output directories.
- Added dataset inventory fingerprints and PLY artifact validation.
- Added wall-time summaries and best-effort per-process VRAM sampling.
- Added the five-run GAJAN LichtFeld reference suite.

## 0.1.0 - Phase 0

- Defined the product boundary between DroneAI and its Gaussian trainer.
- Versioned the initial CLI and run-manifest contracts.
- Added the implementation roadmap and phase gates.
- Added the GPL and third-party provenance register.

The production backend remains LichtFeld until all parity gates pass.
