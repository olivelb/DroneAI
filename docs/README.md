# Documentation index

This index separates the current operational documentation from dated evidence.
The README remains the short project overview; the documents below describe the
implemented system as it exists now.

## Current documentation

| Area | Source of truth |
|---|---|
| System architecture, events, states and artifacts | [`../DOCUMENTATION.md`](../DOCUMENTATION.md) |
| Installation and distributed deployment | [`../DEPLOYMENT.md`](../DEPLOYMENT.md) |
| OVHcloud MKS realistic preproduction | [`OVHCLOUD_PREPROD.md`](OVHCLOUD_PREPROD.md) |
| Qualification, recovery, retention and cost controls | [`OPERATIONS.md`](OPERATIONS.md) |
| Local workflow | [`../LOCAL_PIPELINE.md`](../LOCAL_PIPELINE.md) |
| Development, tests and dependency management | [`../DEVELOPMENT.md`](../DEVELOPMENT.md) |
| Platform versioning and release procedure | [`RELEASES.md`](RELEASES.md) |
| HD facade workflow | [`FACADE_ORTHOPHOTO.md`](FACADE_ORTHOPHOTO.md) |
| Fast aerial alignment and RTK/GCP behavior | [`FAST_ALIGNMENT.md`](FAST_ALIGNMENT.md) |
| Geospatial workspace and AI results | [`GEOSPATIAL_WORKSPACE.md`](GEOSPATIAL_WORKSPACE.md) |
| Adaptive quality profiles and AI confidence policy | [`contracts/quality-profiles-v2.md`](contracts/quality-profiles-v2.md) |
| Mission ownership, catalogue and support scope | [`contracts/mission-ownership-v1.md`](contracts/mission-ownership-v1.md) |
| Versioned stage attempts and immutable artifact DAG | [`contracts/versioned-stage-dag-v1.md`](contracts/versioned-stage-dag-v1.md) |
| Incremental content-addressed artifact manifest | [`contracts/artifact-manifest-v2.md`](contracts/artifact-manifest-v2.md) |
| Machine-readable production drill evidence | [`contracts/production-qualification-evidence-v1.schema.json`](contracts/production-qualification-evidence-v1.schema.json) |
| Audited feature corrections and named raster styles | [`contracts/explorer-editing-styles-v1.md`](contracts/explorer-editing-styles-v1.md) |
| Production boundaries and release gates | [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) |
| DroneGS architecture and backend boundary | [`dronegs/ARCHITECTURE.md`](dronegs/ARCHITECTURE.md), [`dronegs/BACKENDS.md`](dronegs/BACKENDS.md) |
| Trainer command-line contract | [`dronegs/contracts/trainer-cli-v1.md`](dronegs/contracts/trainer-cli-v1.md) |
| Third-party and GPL provenance | [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), [`dronegs/GPL_COMPONENTS.md`](dronegs/GPL_COMPONENTS.md) |

Plans and roadmaps are proposals rather than descriptions of delivered behavior.
They must not override the current documents or executable contracts above.

## Historical evidence

The following files are immutable, dated engineering evidence. They can contain
old names, superseded measurements or conclusions tied to a particular commit,
dataset or machine. They are not operational instructions and do not describe
the current release status:

- [`benchmarks/`](benchmarks/) and [`dronegs/benchmarks/`](dronegs/benchmarks/)
- superseded fixed-cap quality profile contract:
  [`contracts/quality-profiles-v1.md`](contracts/quality-profiles-v1.md)
- machine-readable Helenenschacht sparse/RTK evidence:
  [`benchmarks/helenenschacht-sparse-precision-ab-2026-07-31.json`](benchmarks/helenenschacht-sparse-precision-ab-2026-07-31.json)
- local CUDA 12.9.2 runtime qualification:
  [`benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md`](benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md)
- BIGZEN Villesèque P4 high-quality full E2E, Metashape comparison and
  YOLO/SAM3 qualification:
  [`benchmarks/villeseque-p4-hq-e2e-2026-08-09.md`](benchmarks/villeseque-p4-hq-e2e-2026-08-09.md)
- BIGZEN native DroneGS 12–16 M Gaussian allocation probe:
  [`benchmarks/bigzen-gaussian-capacity-probe-2026-08-10.md`](benchmarks/bigzen-gaussian-capacity-probe-2026-08-10.md)
- BIGZEN Chapelle Banyuls P4 Fast local baseline plus the successful K3s
  five-Job/RTX 3090/SAM3 qualification addendum:
  [`benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md`](benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md)
- implementation plan derived from the BIGZEN E2E operator feedback:
  [`audits/2026-08-09-bigzen-e2e-feedback-plan.md`](audits/2026-08-09-bigzen-e2e-feedback-plan.md)
- 2026-08-06 audit-hardening changes and focused test results:
  [`audits/2026-08-06-audit-hardening-validation.md`](audits/2026-08-06-audit-hardening-validation.md)
- 2026-08-08 platform audit verification and first P1 implementation batch:
  [`audits/2026-08-08-platform-audit-follow-up.md`](audits/2026-08-08-platform-audit-follow-up.md)
- [`audits/`](audits/) and the date-stamped `AUDIT_*` / `CONTRE_AUDIT_*` reports
- [`GAJAN_R2S_VALIDATION.md`](GAJAN_R2S_VALIDATION.md)
- [`dronegs/CHANGELOG.md`](dronegs/CHANGELOG.md)

When historical evidence disagrees with current documentation or code, the
current executable contract and its tests take precedence.
