# GSTile qualified production defaults v1

Policy ID: `gstile-qualified-2026-08-28`. This is the current operational
contract; dated benchmark reports retain their original opt-in decisions.
The operator requested promotion of all qualified upgrades on 2026-08-28,
excluding the OVH Kubernetes API from this rollout.

## Effective defaults

| Boundary | Default | Supported limit / override |
|---|---|---|
| React viewer and qualification harness RAM pack cache | 1,536 MiB, lazy, byte-bounded SLRU | No historical RAM-profile selector |
| Rendering | Merged arena, GPU sort, packed transforms and directional opacity | Tiled/incremental, non-LOD, reference-PLY, CPU sort and float32 renderer alternatives removed |
| Producer library, CLI and `gaussian_viewer` stage | Adaptive-moment V4, proxy size 16,384 | Positive proxy sizes only; other LOD strategies and leaf-only builds are retired |
| Leaves / input chunks | 65,536 / 131,072 records | Smaller leaves require a compatible positive proxy size |
| Pack layout | Depth-spatial aggregation, target 2,097,152 bytes | Positive aggregate targets only; oversized representations retain existing handling |
| Pack preparation | Two workers, 128 MiB pending reservation | `pack_workers=1`; reservation excludes encoder scratch, not a process RSS cap |
| Dedicated CLI and one-shot viewer Job | `OPENBLAS_THREAD_TIMEOUT=16` before numerical imports | Any explicit value, including `0`, wins; thread counts never changed |

The shared Python policy is `shared/gstile_defaults.py`. CLI output, viewer
metadata and stage provenance record `build_configuration` and the policy ID;
recorded effective fields take precedence when explicit overrides are present.
Other one-shot stages, imported modules and long-lived host processes do not
receive the BLAS policy. It is a cycle-counter exponent, not milliseconds.

The RAM ceiling is fixed; the old query profiles are removed. No eager allocation,
browser-memory heuristic or new browser API is introduced. The 75% protected
cache fraction, two IndexedDB slots, six network slots and 2 GiB persistent
cache are unchanged. Browser total-memory qualification was explicitly waived
by the operator; it is not claimed to have passed.

## Existing qualified paths retained

This promotion also retains the already-default merged GPU arena, bounded
worker decode/assembly, SHA verification, buffer reuse, compressed transport
fallbacks, persistent cache, camera prefetch, stale-request handling and bounded
network retries. The V4 flat pair matcher, blocked candidate distances and
bounded-column moment averaging are already in the producer. This change does
not introduce another rasterizer, quantization profile or scientific formula.

Do not activate rejected direct-upload, candidate/refit scratch experiments,
unqualified incremental rendering, larger experimental pack layouts or reduced
BLAS thread counts under the label "all upgrades". No splats are discarded by
the new policy; giant filtering remains opt-in.

## Current format and release handling

Existing bundle IDs, manifests and packs stay immutable. New builds and both
readers support only adaptive V4. Historical profiles are rejected before pack
loading. This cleanup does not migrate or recompute published products.

The ordinary CLI now needs only:

```bash
python tools/build_gstiles.py input.ply NEW_OUTPUT_DIRECTORY
```

The producer no longer supports `--no-lod`, `--individual-packs`, minhash,
spatial-stratified or moment-matched V3 builds. The historical bundle repacker
has also been removed. Library and Stage Job options reject absent LOD/pack
sizes and retired strategies. Worker counts and bounded aggregate/proxy sizes
remain configurable within the same V4 algorithm.

Benchmarks now default to the current V4/aggregate production configuration;
they no longer discover or adapt to historical CLI absence semantics.
The [cleanup record](../audits/2026-08-28-current-production-cleanup.md) lists
the removed reader/rendering branches, hardware image checks and retained safety controls.

This source cleanup does not delete deployed images or immutable objects.
Deploy frontend and producer together and smoke-test on a separate port
before switching an existing listener.
Do not replace a dirty checkout or silently install a new full platform when
only the qualification viewer is running.

## Hardware image checks

Image tests require a hardware WebGPU adapter. The Playwright GSTile project
disables software rasterization and rejects SwiftShader, llvmpipe, lavapipe and
fallback adapters before loading the scene. A ready HUD alone cannot pass: the
test also checks scene pixels outside the HUD. If WSL cannot expose hardware
WebGPU, run the existing suite with Windows Node and Chrome while keeping the
Next server and authoritative source in WSL; see
[Development](../../DEVELOPMENT.md#hardware-webgpu-image-tests).

Historical URL parameters for GPU assembly, RAM profiles, CPU/radial sort,
float32 transforms, opacity, maximum scale, sibling leaves and offscreen
coverage no longer select alternate production paths. Internal scale
validation and coverage defaults remain intact. The qualified opacity shader
sources are unchanged; the renderer always supplies directional mode 1.
Worker/main-thread decoding and transport fallbacks remain supported.

## Evidence and limits

The [2026-08-28 promotion and BIGZEN rollout record](../benchmarks/gstile-production-defaults-qualification.md)
records integrated parity on both hosts, target image identities, the installed
CLI, actual Chrome loading and the exercised container rollback.

- [RAM pilot](../benchmarks/gstile-memory-profile-qualification.md): accepted
  visual conformity and revisits; its recorded network regression is not erased
  by promotion (subsequent freshness fixes remain in the production tree).
- [Pack aggregation](../benchmarks/gstile-pack-aggregation-qualification.md):
  transport layout qualification, not a universal cold-load speedup.
- [Parallel preparation](../benchmarks/gstile-parallel-preparation-qualification.md):
  bounded, byte-identical preparation; gains depend on LOD/pack workload.
- [BLAS idle policy](../benchmarks/gstile-blas-timeout-qualification.md): exact
  output and whole-build gains on the documented MSI runtime, not a BIGZEN claim.

Promotion requires the focused producer/adapter/startup tests, frontend suite,
production build, integrated bundle parity and target smoke checks. Previous
visual approval applies to the unchanged qualified profile; a successful build
or HTTP response alone is not a new visual approval or a full five-stage mission.
