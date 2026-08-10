# Production qualification — bigzen-preprod-2026-08-10

> Generated from the machine-readable qualification record. Edit the JSON source, not this report.

- Gate: **PASSED**
- Evidence source: `bigzen-preprod-2026-08-10.qualification.json`
- Generated at: `2026-08-10T15:06:33.263730Z`
- Environment: `bigzen-preprod` / `bigzen-k3s`
- Namespace: `drone-ai`
- Kubernetes: `v1.36.3+k3s1`
- GPU: `NVIDIA GeForce RTX 3090`, 24576 MiB, driver `591.74`, CUDA `13.1`
- Git commit: `d643002bf21bf08086b143dadc04138838215100`
- Helm chart: `0.1.0`
- RTO/RPO objectives: 900 s / 0 s

## Immutable executor images

| Executor | OCI digest |
|---|---|
| detection | `sha256:533560bfbc8858a3de2370dbbc3cc8cd324e04ee9ad49a1cc555d521f5a9a29d` |
| gaussian_filtering | `sha256:c9d3f5a38d31c85ae58452385e0c416e2dc12e202d1b386707bf32b98ddf9897` |
| gaussian_training | `sha256:c9d3f5a38d31c85ae58452385e0c416e2dc12e202d1b386707bf32b98ddf9897` |
| rasterization | `sha256:c9d3f5a38d31c85ae58452385e0c416e2dc12e202d1b386707bf32b98ddf9897` |
| reconstruction | `sha256:c9d3f5a38d31c85ae58452385e0c416e2dc12e202d1b386707bf32b98ddf9897` |

## Drills

| Drill | Status | RTO | RPO | Runs | Artifacts | Evidence |
|---|---:|---:|---:|---:|---:|---:|
| Complete immutable five-stage chain | passed | 411.192 s | 0 s | 5 | 5 | 2 |
| Cancellation of a running stage | passed | 3.502 s | 0 s | 1 | 0 | 2 |
| Stage deadline expiry | passed | 15.228 s | 0 s | 2 | 0 | 2 |
| Missing Job or pod reconciliation | passed | 2.541 s | 0 s | 1 | 0 | 2 |
| API restart after reservation | passed | 14.158 s | 0 s | 1 | 0 | 2 |
| Database interruption and recovery | passed | 303.392 s | 0 s | 2 | 1 | 2 |
| Object-storage interruption and recovery | passed | 250.495 s | 0 s | 2 | 1 | 2 |
| Database and artifact backup/isolated restore | passed | 3.795 s | 0 s | 0 | 0 | 2 |
| Helm rollback to immutable images | passed | 3.507 s | 0 s | 1 | 0 | 2 |

## Gate findings

All required production drills passed within the recorded objectives.

## Attestation

Operator: `admin@olembo.fr`  
Reviewed at: `2026-08-10T15:06:33.263730Z`

I confirm that this record contains no credentials, signed URLs, private dataset content or raw Terraform state.
