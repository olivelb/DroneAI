# GSTile qualified production defaults v1

Policy ID: `gstile-qualified-2026-08-28`. This is the current operational
contract; dated benchmark reports retain their original opt-in decisions.
The operator requested promotion of all qualified upgrades on 2026-08-28,
excluding the OVH Kubernetes API from this rollout.

## Effective defaults

| Boundary | Default | Explicit rollback / limit |
|---|---|---|
| React viewer and qualification harness RAM pack cache | 1,536 MiB, lazy, byte-bounded SLRU | `gstileMemoryCache=standard`: 768 MiB; `desktop` remains an alias of the default |
| Rendering | Existing merged renderer and qualified worker/GPU optimizations | No promotion of experimental incremental rendering |
| Producer library, CLI and `gaussian_viewer` stage | Adaptive-moment V4, proxy size 16,384 | `lod_proxy_size=None` / CLI `--no-lod`; legacy strategies remain explicit |
| Leaves / input chunks | 65,536 / 131,072 records | Smaller leaves require a compatible explicit proxy size or no LOD |
| Pack layout | Depth-spatial aggregation, target 2,097,152 bytes | `pack_target_bytes=None` / CLI `--individual-packs`; oversized representations retain existing handling |
| Pack preparation | Two workers, 128 MiB pending reservation | `pack_workers=1`; reservation excludes encoder scratch, not a process RSS cap |
| Dedicated CLI and one-shot viewer Job | `OPENBLAS_THREAD_TIMEOUT=16` before numerical imports | Any explicit value, including `0`, wins; thread counts never changed |

The shared Python policy is `shared/gstile_defaults.py`. CLI output, viewer
metadata and stage provenance record `build_configuration` and the policy ID;
recorded effective fields take precedence when explicit overrides are present.
Other one-shot stages, imported modules and long-lived host processes do not
receive the BLAS policy. It is a cycle-counter exponent, not milliseconds.

RAM query values are fixed profiles, never arbitrary sizes. No eager allocation,
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

## Compatibility and rollback

Existing bundle IDs, manifests and packs stay immutable. New builds choose the
previously explicit qualified profile; the format and reader compatibility are
unchanged. Default changes do not migrate or recompute published products.

The ordinary CLI now needs only:

```bash
python tools/build_gstiles.py input.ply NEW_OUTPUT_DIRECTORY
```

To reproduce the former leaf-only/individual/synchronous policy:

```bash
python tools/build_gstiles.py input.ply ANOTHER_NEW_DIRECTORY \
  --no-lod --individual-packs --pack-workers 1
```

Stage `parameters.gaussian_viewer` accepts the corresponding explicit fields:

```json
{"lod_proxy_size": null, "pack_target_bytes": null, "pack_workers": 1}
```

`GsTileBuildOptions` accepts the same overrides. Invalid leaf/proxy combinations
fail rather than silently changing the LOD. Benchmark commands explicitly pin
historical absence semantics using supported rollback flags; their omitted
LOD/pack options must not accidentally become new production defaults.

Retain old images and immutable bundles for operational rollback. Update the
frontend and producer release together, smoke-test on a separate port, then
switch the existing listener with restart of the previous image on failure.
Do not replace a dirty checkout or silently install a new full platform when
only the qualification viewer is running.

## Evidence and limits

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
