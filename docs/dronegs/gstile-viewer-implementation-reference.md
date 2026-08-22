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
  ceiling (2 million by default). Loading the 27-million Saint-Étienne result
  through this baseline would be an uncontrolled multi-gigabyte allocation;
  that scene is a Phase 3 hierarchical-LOD qualification target, not a reason
  to weaken the safety gate.

Remaining exit evidence: real WebGPU shader compilation on the device matrix
and image/directional-alpha comparison against frozen DroneGS references.

### Phase 3 — streaming and replacement LOD

- Build spatial hierarchy and coarse representations out-of-core.
- Select nodes using projected error plus splat and fragment budgets.
- Keep parents until children are complete; cancel stale requests.
- Use optical-thickness transition and deterministic eviction.

Exit: no holes/seams during motion or failed fetches; memory stays under budget.

Implementation status:

- `--lod-proxy-size` opts into the distinct
  `dronegs-sh3-opacity-sh3-q96-minhash-lod-v1` scientific profile; the
  platform stage remains loss-bounded leaf-only unless this option is
  explicitly selected;
- every leaf and source id remains exact. Internal proxies are deterministic
  SplitMix64 min-hash subsets of those source records, constructed bottom-up
  without another pass over the PLY and without changing SH, opacity, scale or
  rotation values before normal q96 encoding;
- every internal node carries a proxy, an explicit geometric-error estimate
  and separate proxy counts/bytes. Python and browser contracts reject missing,
  shared, oversized or profile-inconsistent proxy packs;
- the subset proxy is a navigation approximation, not an optical aggregate.
  It deliberately does not claim density, radiance or directional-opacity
  equivalence to the exact descendants.
- the browser selector uses projected geometric error and a hard resident
  splat budget. It always selects a complete hierarchy cut and resolves equal
  priorities deterministically;
- a replacement is transactional: all missing children are fetched, hashed,
  decoded and uploaded disabled before the previous complete cut is replaced.
  Superseded requests are aborted, and a failed fetch leaves the previous cut
  resident;
- the mission detail page mounts the viewer only when the owner-scoped mission
  publishes `gaussian_viewer_bundle`. The existing signed descriptor endpoint
  remains the only production discovery path.

Remaining exit evidence: camera-path image/seam metrics against the exact
leaves, GPU timestamp telemetry, cache/eviction hysteresis and broader browser
device coverage. These are scientific/performance gates, not blockers for the
validated complete-cut and failure-preservation contracts.

#### Saint-Etienne scale qualification — 2026-08-22

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
leaf-only profile; the min-hash LOD profile is opt-in until its separate
renderer and visual gates pass.

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
- WebGPU Recommendation: https://www.w3.org/TR/webgpu/

These upstream formats and APIs inform the implementation; GSTile remains a
DroneAI-owned contract so the directional-opacity representation cannot be
lost during dependency upgrades.
