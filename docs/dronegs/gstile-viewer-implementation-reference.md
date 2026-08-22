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

### Phase 3 — streaming and replacement LOD

- Build spatial hierarchy and coarse representations out-of-core.
- Select nodes using projected error plus splat and fragment budgets.
- Keep parents until children are complete; cancel stale requests.
- Use optical-thickness transition and deterministic eviction.

Exit: no holes/seams during motion or failed fetches; memory stays under budget.

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
