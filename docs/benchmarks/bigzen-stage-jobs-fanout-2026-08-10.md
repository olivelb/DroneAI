# BIGZEN GPU scheduling and detection fan-out qualification — 2026-08-10

## Verdict

The five-Job pipeline was requalified on BIGZEN at merge commit
`d87ea7800f6c5adf604fc5728e003bdc5c8a819d`. The independent detection
fan-out/fan-in qualification above the former 4,096-tile ceiling remains the
run performed at commit `74b6d7ad3aa5471f9a97a67a5d8fdb999abe4aa7`.
Together, the runs observed the derived 8, 12 and 24 GB GPU selectors on real
K3s Jobs, published strict Artifact Manifest v2 workspaces, persisted one
receipt per detection shard and completed through the fan-in finalizer.

This qualifies the implementation for continued preproduction testing. It
does not qualify OVHcloud production by inheritance: the target cluster still
needs its own image, scheduling, interruption, backup/restore and rollback
evidence.

## Environment and immutable deployment

| Item | Effective value |
| --- | --- |
| Host | BIGZEN, Ubuntu WSL2, K3s `v1.36.3+k3s1` |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB, driver 591.74 |
| RAM | 94 GiB visible, no swap |
| Fan-out application commit | `74b6d7ad3aa5471f9a97a67a5d8fdb999abe4aa7` |
| Final five-Job commit | `d87ea7800f6c5adf604fc5728e003bdc5c8a819d` |
| COLMAP and IA images | Commit-derived full-SHA tags |
| CUDA/COLMAP base | Existing `drone-colmap-base:latest` reused; not rebuilt |
| Artifact writer | Manifest v2 enabled |
| Detection rollout | Selective restore and fan-out enabled only after the five-Job pass |

The final five-Job requalification used Helm release revision 8 and full-SHA
application image tags at `d87ea7800f6c5adf604fc5728e003bdc5c8a819d`.
The existing CUDA/COLMAP base was reused: no CUDA or COLMAP build was run.

The RTX 3090 node advertised the cumulative capabilities
`gpu-vram-at-least-8gb=true`, `gpu-vram-at-least-12gb=true` and
`gpu-vram-at-least-24gb=true`. Every GPU Job also carried the narrow
`nvidia.com/gpu=present:NoSchedule` toleration, `runtimeClassName: nvidia` and
one `nvidia.com/gpu` limit.

## Five-Job scheduling and Manifest v2 pass

Mission `chapelle-v2-gpu-e2e-20260810` reused the sealed 114-image Chapelle
dataset with the `fast-v1` profile and SAM3 prompt `car`. It reached success at
100% with no retry.

| Stage | Resource class / observed selector | Duration | Result |
| --- | --- | ---: | --- |
| Reconstruction | `gpu-geometry` / at least 12 GB | 3 min 35 s | succeeded |
| Gaussian training | `gpu-high-memory` / at least 24 GB | 50 s | succeeded |
| Gaussian filtering | `gpu-high-memory` / at least 24 GB | 16 s | succeeded |
| Rasterization | `gpu-standard` / at least 8 GB | 26 s | succeeded |
| SAM3 detection | `gpu-high-memory` / at least 24 GB | 1 min 37 s | succeeded |

The raster was 6,937 × 6,798 pixels and detection processed 81 overlapping
1,024 px tiles. The reconstruction manifest was parsed independently as schema
2: all 259 entries referenced `blobs/sha256/...` keys. The five published
logical workspaces retained their immutable artifact IDs and SHA-256 checksums.

## Fan-out above 4,096 tiles

Mission `villeseque-reference-fanout-4160-20260810` used a central
12,416 × 12,350 crop of the Metashape Villesèque orthomosaic. The source crop
was published as a one-file Manifest v2 raster artifact before the detection
stage was released.

| Plan field | Value |
| --- | ---: |
| Tile size / overlap | 256 / 64 px |
| Total tiles | 4,160 |
| Tiles per shard | 1,024 |
| Shards | 5 |
| Indexed Job parallelism | 2 |
| Plan checksum | `d8a30eb7cd1cc49b0cdfd7c6c6718b624daec5dd87e8219d52ec88ef5c2bd414` |
| Planned source pixels | 272,629,760 |
| Pixel amplification | 1.777971 |

K3s exposed one physical GPU allocation slot, so one shard ran while the next
remained Pending. The four 1,024-tile shards took about 5 min 38 s each; the
64-tile tail then completed. The Indexed Job reached 5/5 in about 23 minutes.
All five receipts were durable before the finalizer was dispatched:

| Shard | Tiles | Receipt bytes |
| ---: | ---: | ---: |
| 0 | 1,024 | 640 |
| 1 | 1,024 | 875 |
| 2 | 1,024 | 640 |
| 3 | 1,024 | 4,666 |
| 4 | 64 | 1,275 |

The finalizer completed in 6 seconds. It aggregated 16 raw masks into 10
geolocated features and published artifact
`fcabca51-abc0-59e9-9488-974c5935e4bc`, checksum
`053fa5fe1bf7b67762279c09e9d48338b72d1b9f2ba1ca042c9bd4670544b5dd`.
Selective materialization downloaded only `orthomosaic.tif`. The output
manifest reused all 381,503,462 parent bytes and transferred only 23,165 new
bytes.

An initial boundary canary planned exactly 4,096 tiles because the planner
merges a near-edge window. It was cancelled through the operator API; the
mission became terminal and its Indexed Job was deleted before the corrected
4,160-tile mission was launched.

## SAM3 resource-policy finding

The active SAM3 process used a stable sampled 6,334 MiB of VRAM. Each tile was
processed sequentially while one model instance remained resident. The pinned
processor configuration resizes every image to 1,008 × 1,008, so the 256 px
canary windows were upscaled before inference. Consequently, the source-pixel
planning metric does not represent the effective model pixels, and the current
blanket `sam3 -> gpu-high-memory` mapping is conservative rather than measured.

Follow-up work must make the SAM3 envelope depend on the immutable model
revision, effective processor resolution, dtype and bounded batch size. It
must record CUDA peak allocated/reserved memory, keep a controlled OOM fallback
and avoid small tiles that are merely upscaled. That change needs a focused
SAM3 GPU E2E on BIGZEN; it does not require replaying reconstruction or Gaussian
training.

### Batch-one policy requalification

Commit `745e6815a444aad1db9dd3d37149bb8264d0e50a` implemented the safe
batch-one part of that follow-up. The shared capability contract pins model
revision `3c879f39826c281e95690f02c7821c4de09afae7`, a 1,008 px processor
target, a maximum 1,024 px source tile, batch size one and a 12 GiB minimum
VRAM envelope. Both API validation and the stage worker reject larger SAM3
source tiles; YOLO keeps its independent 4,096 px upper bound. The worker also
refuses a pinned processor whose runtime size no longer matches the contract.

Detection attempt `8486f140-780d-42d6-b9dc-6ee502c1b57f` reused the existing
Chapelle raster artifact and immutable executor image `drone-ia:745e6815...`.
The generated Job requested `gpu-geometry`, selected
`droneai.io/gpu-vram-at-least-12gb=true` and completed all 81 tiles in 92.125
seconds of stage execution (102 seconds including Job startup). External
500 ms sampling observed a 6,334 MiB maximum for total GPU memory. The
published model manifest recorded CUDA BF16, batch one and 1,008 × 1,008
processor input. Selective restore transferred 121,080,082 bytes and publish
transferred 3,199 bytes while reusing 1,585,346,824 logical bytes.

This qualifies the conservative 12 GiB batch-one class on Ampere without
claiming that an 8 GiB device is safe. CUDA allocated/reserved peak telemetry
and an explicitly bounded multi-image batch remain separate optimizations; a
24 GiB class can still be requested, but batch one intentionally does not
consume the extra memory.

## Post-merge five-Job requalification

Mission `chapelle-d87-five-jobs-20260810` exercised the complete DAG after the
shared stage-execution and workspace refactors were merged. It reused the
sealed 114-image Chapelle dataset, selected `fast-v1`, SAM3 prompt `car`,
1,024 px source tiles and the 1.5 million Gaussian profile cap. The mission
completed at 100% with one attempt, five successful Kubernetes Jobs and five
published products in 6 min 53 s wall-clock time.

| Stage | Resource class / selector | Stage execution | Result |
| --- | --- | ---: | --- |
| Reconstruction | `gpu-geometry` / at least 12 GiB | 3 min 24 s | succeeded |
| Gaussian training | `gpu-high-memory` / at least 24 GiB | 56 s | succeeded |
| Gaussian filtering | `gpu-high-memory` / at least 24 GiB | 16 s | succeeded |
| Rasterization | `gpu-standard` / at least 8 GiB | 26 s | succeeded |
| SAM3 detection | `gpu-geometry` / at least 12 GiB | 85 s | succeeded |

All five Jobs used the full immutable application SHA. COLMAP stages used
`drone-colmap:d87ea7800f6c5adf604fc5728e003bdc5c8a819d`; detection used
`drone-ia:d87ea7800f6c5adf604fc5728e003bdc5c8a819d`.

The reconstruction registered all 114 images and produced 19,875 sparse
points with 1.290 px mean reprojection error. Training produced 165,300
Gaussians and filtering retained 163,444 (98.88%). Rasterization produced a
7,108 × 6,856 EPSG:3942 orthomosaic; every enforced coverage check passed.
SAM3 planned and processed 81 tiles, used CUDA BF16 with batch size one and a
1,008 × 1,008 processor target, and published one deduplicated geolocated
`car` feature.

External two-second sampling observed about 2,580 MiB during the fast-profile
Gaussian training interval and 6,334 MiB during SAM3 inference. These values
qualify the batch-one SAM3 12 GiB scheduling class on this Ampere GPU. They do
not reduce the 24 GiB production envelope for Gaussian training/filtering:
that class must also cover the larger `normal-v1` and `hq-v1` profiles and
needs separate peak telemetry before it can be tightened.

Artifact Manifest v2 and incremental workspaces were verified from the live
object store. The final detection manifest had `schema_version: 2`, stored its
two new detection files as content-addressed `blobs/sha256/...` entries and
referenced the raster parent manifest. Detection selectively materialized only
`orthomosaic.tif`: one file and 124,809,785 transferred bytes instead of the
1,603,148,636-byte logical raster workspace. Publishing the detection result
then reused 1,603,148,636 bytes and transferred only 5,556 new bytes.

This run qualifies the merged five-Job implementation on BIGZEN. It does not
replace the existing greater-than-4,096-tile fan-out run, and neither result
automatically qualifies a different OVH node pool, driver or image digest.

## Remaining release gates

- Repeat scheduling and fan-out canaries on the exact OVH node pool and image
  digests before enabling the flags there.
- Exercise the operator workflow with multiple products and confirm that
  cancellation, progress, logs and product selection remain mission-scoped.
- Complete the production interruption, deadline, database/object-storage,
  backup/restore and Helm rollback drills.
- Treat the fan-out run's ten SAM3 features and the requalification run's one
  feature as pipeline evidence only; neither run provided labelled
  precision/recall ground truth.
