# DroneGS tiler and GSTile viewer — implementation reference

This document is the delivery reference for the DroneAI Gaussian streaming
work. It refines the architecture proposal into ordered, testable increments.
Scientific representation and platform orchestration are tracked separately.

## Decisions

1. **Rendering base:** integrate PlayCanvas behind a DroneAI
   `GaussianRenderBackend` adapter. The application, cache and GSTile contract
   must not depend on PlayCanvas internals.
2. **Format:** use the DroneAI GSTile contract. Standard Streamed SOG is a
   benchmark/import path, not the source of truth, because it does not encode
   DroneGS directional opacity.
3. **Reference profile:** ship the loss-bounded 96-byte profile first. Only
   promote a codebook profile after objective parity and device benchmarks.
4. **Storage:** immutable bundle + immutable packs; manifest URLs are short
   lived, pack URLs/ranges are refreshable and cacheable by content identity.
5. **LOD:** hierarchical replacement. A parent disappears only after all
   selected children are resident. Transitions use optical thickness, never a
   direct linear alpha blend.
6. **Coordinates:** float64 world/origin handling on CPU, quantized tile-local
   positions on GPU. CRS and local origin are explicit metadata.
7. **Interaction:** stable source ids are mandatory. Measurement uses a
   collision representation, not rendered splat centres.
8. **Pipeline:** `gaussian_viewer` depends on `gaussian_filtering` and is a
   sibling of `rasterization`; viewer failure cannot invalidate raster output.
9. **Engine integration:** pin PlayCanvas `2.21.4` and use its public unified
   GSplat extension points. `GSplatFormat.addExtraStreams()` stores DroneGS
   opacity data on each source resource, while
   `GSplatComponent.setWorkBufferModifier()` evaluates directional alpha during
   the source-to-work-buffer copy. The normal unified work-buffer and WebGPU
   global sorter remain unchanged. A maintained engine fork is not justified
   unless a later parity/streaming gate proves these public hooks insufficient.

## Corrected delivery order

The architecture proposal is sound, but the format/parity gate precedes the
renderer fork. Otherwise a convenient renderer layout could accidentally
become the long-lived artifact contract.

### Phase 0 — contracts and representative corpus

- Freeze GSTile v1, directional-opacity math, coordinate frame and stable ids.
- Select tiny, medium and real 15-cell PLY fixtures.
- Capture DroneGS reference renders/directional-alpha samples.
- Define quality, memory, disk, latency and browser-device matrices.

Exit: contract tests and reference hashes are reviewable without a GPU.

### Phase 1 — deterministic baseline tiler

- Stream binary PLY without whole-file materialization.
- Validate schema and finite values.
- Partition spatially in bounded temporary storage.
- Quantize to the baseline profile, write atomically, hash and validate.
- Report field-wise error bounds, bytes/splat, peak working set and disk needs.

Exit: repeatable bundle hashes, corruption tests, bounded-memory benchmark.

### Phase 2 — browser reader and resident parity

- Strict manifest decoder and safe relative-path resolver.
- Abortable HTTP-range scheduler with bounded concurrency and retries.
- CPU decoder, GPU upload arena and explicit resource lifecycle.
- Resident renderer with exact colour SH and opacity-logit SH formula.
- PlayCanvas adapter is pinned to a reviewed engine revision.

Exit: same resident GSTile scene matches DroneGS references within frozen
thresholds; unsupported WebGPU produces an actionable fallback.

Implementation status:

- strict manifest/range/pack decoders, SHA-256 and CRC32 validation are present;
- the 96-byte record is expanded to the PLY property convention expected by
  PlayCanvas without changing coefficient order;
- four RGBA32F resource streams preserve the base logit and all 15 opacity-SH
  coefficients; the work-buffer receives only the evaluated alpha;
- PlayCanvas uses `GSPLATDATA_LARGE`, GPU global sorting and continuous SH
  refresh while the camera moves;
- the resident adapter deliberately rejects scenes above its configured splat
  ceiling (6 million by default). Loading the 49-million Saint-Étienne result
  through the exact baseline would still be an uncontrolled multi-gigabyte
  allocation; that scene remains a Phase 3 hierarchical-LOD qualification
  target rather than a reason to remove the safety gate.

Remaining exit evidence: real WebGPU shader compilation on the device matrix
and image/directional-alpha comparison against frozen DroneGS references.

### Phase 3 — streaming and replacement LOD

- Build spatial hierarchy and coarse representations out-of-core.
- Select nodes using projected error plus splat and fragment budgets.
- Keep parents until children are complete; cancel stale requests.
- Use optical-thickness transition and deterministic eviction.

Exit: no holes/seams during motion or failed fetches; memory stays under budget.

Implementation status:

- `--lod-proxy-size` opts into the distinct replacement-LOD scientific family;
  the platform stage remains loss-bounded leaf-only unless this option is
  explicitly selected. New builds use
  `dronegs-sh3-opacity-sh3-q96-moment-lod-v3`. The CLI retains explicit
  `spatial-stratified` and `minhash` strategies only to reproduce V2 and V1;
- every leaf and source id remains exact. V3 sorts each local population by a
  deterministic 63-bit Morton code, then merges bounded spatial strata. Each
  proxy preserves opacity-area mass, weighted colour/directional-opacity SH,
  the weighted centre, and full covariance through the law of total variance;
  covariance eigendecomposition reconstructs its anisotropic scale and
  rotation. Propagated geometric error includes the current merge radius and
  all descendant approximation error;
- V2 selected one unchanged source Gaussian per Morton stratum. It fixed the
  gross spatial concentration of V1 but did not conserve radiance or occupied
  support; shader-side scale inflation produced the noisy distant facade and
  uneven close detail seen in real RTX 4070 qualification. V1 used deterministic
  SplitMix64 min-hash subsets and concentrated detail in a few regions. Python
  and browser contracts retain both historical profiles for reproducibility,
  not as quality targets;
- every internal node carries a proxy, an explicit geometric-error estimate
  and separate proxy counts/bytes. Python and browser contracts reject missing,
  shared, oversized or profile-inconsistent proxy packs;
- V3 proxies already encode their merged covariance, so the renderer disables
  the historical shader-side scale multiplier for this profile;
- the browser selector uses projected geometric error and a hard resident
  splat budget. Its conservative bounding-sphere frustum test omits branches
  that cannot contribute to the current image, so a close view spends its
  budget on visible descendants instead of nearby off-screen geometry. It
  computes error against the nearest conservative node depth, reports when the
  splat budget blocks refinement, always selects a complete visible hierarchy
  cut and resolves equal priorities deterministically;
- missing ranges are fetched, hashed and decoded into CPU-side prepared tiles.
  Superseded consumers are aborted, but identical range transfers are shared
  and completed into a 768 MiB LRU cache instead of being downloaded again.
  Replacement groups are committed progressively: refinement removes an
  ancestor only when every selected child in that group is ready, and
  coarsening removes descendants only when their ancestor is ready. Unrelated
  obsolete nodes are released early, coarsening is committed before
  refinement, and a hard pre-commit check prevents transient GPU residency
  from exceeding the configured splat budget. A failed fetch leaves the
  previous representation resident. The HUD distinguishes a stable cut,
  active refinement, a budget limit and an error, and reports the
  current/target populations and remaining nodes;
- the mission detail page mounts the viewer only when the owner-scoped mission
  publishes `gaussian_viewer_bundle`. The existing signed descriptor endpoint
  remains the only production discovery path;
- the PlayCanvas adapter enables antialiasing, radial sorting and low-alpha
  rendering, and accepts subpixel splats (`minContribution=0.05`,
  `minPixelSize=0.5`). The previous defaults culled much of a distant facade
  before the LOD budget could contribute it.

Remaining exit evidence: camera-path image/seam metrics against the exact
leaves, GPU timestamp telemetry, cache/eviction hysteresis and broader browser
device coverage. These are scientific/performance gates, not blockers for the
validated complete-cut and failure-preservation contracts.

#### Saint-Etienne legacy min-hash scale qualification — 2026-08-22

The production-sized qualification used the immutable 14,624,789,799-byte
Saint-Etienne seam-safe PLY (49,408,067 Gaussians). It did not repeat COLMAP,
DroneGS training or filtering.

| Evidence | Result |
|---|---|
| Exact default compatibility | The pre/post-LOD 5,000-Gaussian fixture retained identical bundle, manifest and all eight pack hashes. |
| Hierarchical build | 1,350 exact leaves + 1,349 internal proxies, 2,699 packs, 5,804,157,568 pack bytes. |
| Proxy overhead | 11,051,008 proxy records and 1,060,939,936 proxy bytes; exact leaves remain 4,743,217,632 bytes. |
| Build resource bound | 14 min 49.30 s wall time, 324,760 KiB maximum RSS, no swap. |
| Immutable identity | Bundle `sha256:8fbebcbe571e8bd8af6bb5504706733b213c1847d15bac8eb9e773db146bd6ba`; manifest `sha256:177e04df6536a5af80a2f2e5140b985892c5abce367abdb1ed13ee585bb052d3`. |
| Independent validation | All 2,699 sizes and SHA-256 hashes, 5,804,157,568 bytes, validated in 71.84 s with 66,624 KiB maximum RSS. |
| Generic browser harness | The real LOD bundle renders, zoom triggers additional range requests while preserving a complete budgeted cut, and the 49-million exact profile is rejected before any pack request. |
| RTX 3090 qualification | Edge 151 WebGPU reported `vendor=nvidia`, `architecture=ampere`; initial cut 23 nodes / 198,029 splats; zoom caused new pack requests while remaining ready and within the 2-million-splat ceiling. |

The Ubuntu/WSL Chromium build exposes WebGPU through SwiftShader because this
host has no NVIDIA Vulkan ICD for WSL. That path passes the same functional
test but is not hardware-performance evidence. Hardware qualification therefore
uses Windows Edge/D3D12 on the same BIGZEN RTX 3090. Logs and captures remain
under `I:\DroneAI-GSTile-Tests\viewer-qualification-ecb4af8`; temporary Edge,
Node/CDP and Next processes are stopped after the test.

#### Saint-Etienne stratified LOD V2 qualification — 2026-08-22

The V2 qualification rebuilt only the GSTile hierarchy from the same immutable
14,624,789,799-byte, 49,408,067-Gaussian PLY. It did not rerun COLMAP,
DroneGS, filtering or orthophoto generation.

| Evidence | Result |
|---|---|
| Spatial hierarchy | 1,350 exact leaves + 1,349 stratified proxies, 2,699 packs and 11,051,008 proxy records. |
| Bundle size | 4,743,217,632 exact-leaf bytes + 1,060,939,936 proxy bytes = 5,804,157,568 bytes. |
| Build resource bound | 15 min 14.46 s wall time, 305,872 KiB maximum RSS, no swap. |
| Immutable identity | Bundle `sha256:3c456427fd2e19ce3b8d2b01fd5ce1196f55efd7a3a9b667202afe33eb1980cc`. |
| Exact-data preservation | The ordered `{id, sha256, recordCount}` fingerprint of all non-LOD packs is identical to the legacy bundle: `50d309bda4556aeb0078adf623ad21b634945c642770ed8e7dd6da0d1b70bdf3`. |
| Independent validation | Every declared size and SHA-256 hash of all 2,699 pack files was revalidated successfully. |
| RTX 4070 distant view | Chrome/WebGPU remains ready at the 6,000,000-splat ceiling with 403 resident hierarchy nodes; the complete facade is represented instead of concentrating detail in one sampled region. |
| RTX 4070 close view | Zoom on the rose window remains ready with 366 resident nodes. Frustum-aware selection replaces visible branches while releasing off-screen branches, so detail is distributed over the complete visible region. |
| Streaming evidence | 4,227 HTTP range requests, 6,617,799,936 bytes served and zero range-server errors across the distant, zoom and navigation checks. |

The test also confirmed screen-aligned pan in focused unit coverage and exposed
both right-button drag and Shift-drag in the UI. Rotation and zoom were
exercised in the real Chrome viewer. Image parity with the 1 mm orthophoto is
not claimed: the GeoTIFF is a fixed facade projection, while this viewer keeps
a bounded, navigable 3D hierarchy. The valid gate here is consistent spatial
coverage and progressive visible detail under a fixed VRAM budget; exact-leaf
and frozen DroneGS render comparisons remain the scientific fidelity gate.

The qualified bundle is preserved under
`I:\DroneAI-GSTile-Tests\saint-etienne-facade-1mm-stratified-v2-5b96d2e\bundle`.
The legacy bundle and all earlier evidence remain intact for comparison.

#### Saint-Etienne moment-matched LOD V3 qualification — 2026-08-22

V3 was built from the same immutable source PLY. Its deterministic spatial
groups replace representative-source proxies with moment-matched Gaussians;
exact leaves remain unchanged.

| Evidence | Result |
|---|---|
| Bundle | 1,350 exact leaves + 1,349 moment-matched proxies, 2,699 packs, 5.41 GiB; bundle `sha256:7c1cdb6cf38cb2aacc75ceac1e2387ecf8e980a00d9367b8333bdbcb5d448637`. |
| Integrity | All declared pack sizes and SHA-256 hashes were independently validated. |
| Distant RTX 4070 view | Antialiasing plus subpixel contribution restores facade coverage that the previous renderer defaults discarded. The selector remains explicitly `budget-limited` at 6,000,000 splats rather than claiming full 49.4-million-splat fidelity. |
| Progressive zoom | A real Chrome wheel zoom converged from 3,487,724 resident splats / 181 pending nodes to 5,994,411 / 0 in about 8 seconds, with visible intermediate updates every second. |
| VRAM bound | No transient overshoot was observed: resident count stayed below 6,000,000 while CPU-prepared transition groups were committed atomically. RTX 4070 VRAM remained stable at about 6.2 GiB used. |
| Directional opacity audit | Over 8,192 V3 proxies evaluated in 32 directions, absolute directional-alpha error had median 0.00212, p90 0.01317, p99 0.04322 and mean 0.00518. This is measurable scientific approximation, but it does not explain the former gross missing-tile behaviour. |

The qualification separates two limits. Missing refreshes and distant
subpixel loss were renderer/streaming defects and are corrected. Remaining
distant-view error is chiefly a scientific LOD issue: a fixed Morton stratum
is less adaptive than the pairwise merge/re-cost decimator used by PlayCanvas
SplatTransform, while 6 million resident Gaussians cannot equal all 49.4
million exact leaves. V4 therefore uses adaptive spatial merge candidates and
fits the nonlinear directional opacity response. It retains a distinct
scientific profile and must pass frozen-render gates before replacing V3.

#### Adaptive LOD V4 implementation — 2026-08-22

V4 is an explicit opt-in profile,
`dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4`, selected with
`--lod-proxy-strategy adaptive-moment`. V1–V3 and the loss-bounded leaf-only
default remain reproducible; V4 is not silently substituted into an existing
scientific contract.

- deterministic Morton-neighbour candidates are scored with scene-normalized
  geometry, log-scale, colour DC, base-opacity and directional-opacity costs;
- cost-ordered pair matching runs in bounded generations and always completes
  its exact target population with a deterministic Morton fallback;
- every merged Gaussian conserves opacity-area mass, weighted centre and full
  covariance. Its anisotropic scale and rotation come from covariance
  eigendecomposition;
- directional opacity is no longer obtained only by linearly averaging SH
  coefficients. For 32 deterministic equal-area directions, V4 evaluates each
  source sigmoid, aggregates directional optical mass, converts the target
  alpha back to logits and least-squares fits the degree-0..3 coefficients.
  The manifest statistic is
  `deterministic-adaptive-cost-moment-opacity-refit-v4`, which prevents a
  pre-refit experimental bundle from being accepted as the final V4 contract;
- each node carries `renderBounds`, the union of the exact leaves' anisotropic
  three-sigma support. Frustum culling uses this support instead of centre-only
  bounds, preventing a large splat from contributing on screen while its leaf
  is incorrectly considered invisible;
- proxy SSE is bounded by both centre displacement and the proxy's largest
  one-sigma radius. This prevents a covariance-expanded moment proxy with
  nearly unchanged centres from declaring negligible error and remaining
  visibly blurred at close range;
- proxy SSE also includes its projected screen footprint. A proxy that covers
  a large part of the viewport is therefore refined even when its stored
  world-space displacement and covariance errors are both optimistic. This
  removes the stable sharp-leaf/blurred-proxy rectangles observed at strong
  zoom; the term is folded into the same global SSE budget instead of adding a
  camera-specific scientific threshold;
- selected requests are ordered by projected screen support and keep sibling
  requests contiguous. The range scheduler uses eight concurrent transfers,
  gives an orphaned transfer a 300 ms reuse window across adjacent camera
  selections, retains truly shared transfers and avoids hashing the same
  cached `ArrayBuffer` twice;
- the viewer stages the complete next camera-selected cut in CPU memory while
  the previous complete cut remains displayed. Only after every selected node
  is decoded, hash-validated and known to fit the resident budget does it
  replace the complete GPU cut between two rendered frames. This scene-wide
  atomic replacement is intentional: Gaussian proxies have no independently
  blendable seam skirt, so committing ready branches progressively exposes a
  visible checkerboard of coarse and exact representations.

Focused evidence currently passes 13 Python tests, the complete 96-test
frontend suite, linting, typechecking and the production
Next.js build. A 65,536-to-8,192 proxy benchmark takes 1.82 s with
the nonlinear fit versus 1.52 s before it. The synthetic directional-opacity
case reduces mean squared alpha error by about 55% relative to linear SH
averaging. The production-sized Saint-Etienne build and RTX 4070 camera-path
captures remain the final fidelity/performance gates.

The RTX 4070 R6 qualification raises the default resident ceiling from
6,000,000 to 7,000,000 splats. At the reproduced strong-zoom rose-window
camera, the viewer converged to a complete 171-node cut with 5.7 million
resident splats and no isolated blurred proxy tile. Under the 7-million-splat
ceiling Windows reported 7.54 GiB dedicated and 2.21 GiB shared GPU memory;
Chrome/WebGPU remained operational without a device-loss or out-of-memory
error. This higher ceiling is an operational viewer setting only and does not
change the source PLY, proxy construction or scientific content.

### Phase 4 — adaptive 4K and telemetry

- Measure frame CPU, GPU (when timestamp queries exist), sorting, decode,
  upload, network and cache separately.
- Adapt render scale, splat budget and SH degree with hysteresis.
- Never use noisy single-frame decisions or synchronously read GPU results.

Exit: sustained target frame time on the agreed hardware/browser matrix.

### Phase 5 — interaction, geospatial and edits

- Stable-id picking, hover throttling and selection sets outside React state.
- Measurement on SVO/mesh collision data with CRS-aware coordinates.
- Immutable edit overlays and auditable export; do not rewrite source packs.
- Shared 2D/3D workspace tools and explicit capability negotiation.

Exit: deterministic measurement/edit tests and cross-view selection parity.

### Phase 6 — platform integration and hardening

- Add the CPU `gaussian_viewer` stage after filtering, parallel to rasterization.
- Publish `gaussian_viewer_bundle` with lineage and immutable identity.
- Add authorized manifest discovery and pack URL refresh APIs.
- Add quotas, cancellation, observability, recovery and object-store tests.

Exit: stage/API failure isolation, tenant authorization tests and production
runbook are complete.

Implementation status:

- DAG v2 includes the independent, non-blocking `gaussian_viewer` branch after
  `gaussian_filtering`; rasterization and detection retain scheduling priority;
- the CPU-high-memory executor consumes the immutable filtering artifact,
  reuses the seam-safe resident partition merge when needed, and never repeats
  DroneGS training or filtering;
- only the derived GSTile product is published. Artifact edges retain exact
  input lineage without copying the filtering workspace into the bundle;
- the owner-scoped API validates the tenant CAS workspace, manifest paths,
  pack sizes and SHA-256 identities before returning short-lived signed pack
  URLs; pack bytes are never proxied through the API;
- Helm production/preproduction contracts, scoped executor secrets, range CORS,
  cancellation checkpoints, schema migration and non-blocking mission
  projection are wired and covered by focused tests.

Remaining exit evidence: real S3/OVH range qualification, cancellation during
a multi-pack build, SaaS quota policy for bundle bytes, operational alerts and
the production runbook. These platform tasks do not authorize Phase 3 LOD
approximations. The production stage default remains the loss-bounded
leaf-only profile; moment-matched replacement LOD remains opt-in until its real
Saint-Etienne bundle and renderer visual gates pass. V1 and V2 are retained for
backward compatibility, not as quality targets.

## Non-negotiable separation

Platform changes include storage layout, streaming, caching, scheduler,
telemetry, API authorization and renderer resource management. Scientific
changes include SH truncation, codebook fitting, opacity approximation, LOD
aggregation, geometry collision generation and any Gaussian removal. Each
scientific approximation needs a named profile, metrics and its own review.

## Measurable gates

| Gate | Minimum evidence |
|---|---|
| Fidelity | resident reference images + directional-alpha samples |
| Determinism | identical bundle and pack hashes on two builds |
| Scale | bounded RSS and temporary disk on a real multi-cell PLY |
| Streaming | range/cancel/retry/cache integration tests |
| LOD | camera-path captures with no missing representation |
| Performance | p50/p95 frame, sort, decode, upload and request latency |
| Compatibility | Chrome/Edge WebGPU plus explicit unsupported path |
| Security | tenant authorization, traversal, malformed-count, decompression limits |
| Operations | stage isolation, idempotency, cancellation and artifact lineage |

## Primary upstream references

- PlayCanvas Streamed SOG v1 specification:
  https://developer.playcanvas.com/user-manual/gaussian-splatting/formats/streamed-sog/
- PlayCanvas engine: https://github.com/playcanvas/engine
- PlayCanvas `splat-transform` streaming/LOD writer and adaptive decimator:
  https://github.com/playcanvas/splat-transform/tree/main/src/lib
- PlayCanvas moment-matched Gaussian merge:
  https://github.com/playcanvas/splat-transform/blob/main/src/lib/decimate/moment-match.ts
- PlayCanvas adaptive merge re-costing:
  https://github.com/playcanvas/splat-transform/blob/main/src/lib/decimate/select-recost.ts
- SuperSplat viewer streaming budget and loading state:
  https://github.com/playcanvas/supersplat-viewer/blob/main/src/viewer.ts
- Hierarchical 3D Gaussians (official implementation):
  https://github.com/graphdeco-inria/hierarchical-3d-gaussians
- Hierarchical 3D Gaussians viewer/LoD implementation:
  https://github.com/FelixWindisch/hierarchical-LOD-gaussians
- A LoD of Gaussians (SIGGRAPH 2026 official implementation):
  https://github.com/FelixWindisch/LoDOfGaussians
- WebGPU Recommendation: https://www.w3.org/TR/webgpu/

These upstream formats and APIs inform the implementation; GSTile remains a
DroneAI-owned contract so the directional-opacity representation cannot be
lost during dependency upgrades.
