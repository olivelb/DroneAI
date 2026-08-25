# GSTile pack aggregation qualification — Saint-Étienne

Date: 2026-08-26

## Scope

- Source bundle: `saint-etienne-facade-1mm-adaptive-opacity-v4c-filtered-r1`
- Source bundle ID: `sha256:e0418a018e8898f4a8bf6fcd73964d3e4be938b069578eb8e6f69539acc174c1`
- Source population: 49,392,943 Gaussians
- Representations: 1,340 exact leaves and 1,339 LOD proxies
- Browser: Chrome, cold immutable bundle URLs
- Qualification view: recommended facade camera, final cut of 7.4 million
  Gaussians and 356 selected nodes (138 exact leaves, 218 proxies)
- Visual acceptance criterion: indistinguishable from the original PLY

Aggregate repacking copies every canonical Q96 tile payload byte for byte. It
does not decode, requantize, or regenerate a Gaussian.

## Measurements

| Layout | Target | Requests/objects | Network bytes | Initial LOD | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| One representation per pack | — | not recorded | 1,038,925,820 | 10.257 s | reference |
| Source-order aggregate | 8 MiB | 341 | 2,106,712,041 | 24.096 s | rejected |
| Source-order aggregate | 4 MiB | 495 | 1,402,551,357 | 14.806 s | rejected |
| Depth-spatial aggregate | 2 MiB | 517 | 1,035,636,094 | 12.354 s | accepted |

The 16 MiB source-order run was excluded from timing because Chrome rejected
one decoded response after a raw fallback. A disk audit confirmed matching raw
and encoded SHA-256 values and exact zstd round-trip bytes; the renderer failed
closed and displayed no corrupt geometry.

The cold-load numbers are single-run observations and include local server,
browser scheduling, and storage variance. They support layout selection, not a
claim that the 2 MiB layout is universally faster than the reference.

## Cut-overfetch analysis

The selected cut contained 713,793,120 useful raw payload bytes. Replaying its
node identifiers against candidate layouts produced:

| Layout | Target | Estimated requests | Raw efficiency |
| --- | ---: | ---: | ---: |
| Source-order | 4 MiB | 332 | 64.15% |
| Depth-spatial | 2 MiB | 325 | 99.41% |
| Depth-spatial | 4 MiB | 268 | 77.67% |
| Depth-spatial | 8 MiB | 179 | 56.12% |

Source-order proxy packs mixed tree depths. A LOD frontier normally selects a
parent or its descendants, not both, so those representations are nearly
mutually exclusive. `depth-spatial-v1` groups one representation kind at one
tree depth in node-ID order. This removes the systematic parent/descendant
overfetch while retaining limited request coalescing.

## Qualified artifact

- Path: `saint-etienne-facade-1mm-adaptive-opacity-v4c-filtered-pack2m-depth-r1/bundle`
- Bundle ID: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`
- Canonical packs: 2,554
- Canonical bytes: 6,847,869,152
- Repack elapsed time: 572 seconds
- Visual comparison against the original PLY: accepted by the operator on
  2026-08-26

## Decision

- Keep aggregate-pack support and the lossless repacker.
- Require `depth-spatial-v1` grouping for generated aggregate layouts.
- Recommend an explicit 2 MiB target for the qualified Saint-Étienne profile.
- Keep one-representation-per-pack as the default until multiple scenes and
  camera trajectories demonstrate a repeatable end-to-end speedup.
- Do not use source-order aggregation or 4–16 MiB targets for this profile.
