# Documentation index

This index separates the current operational documentation from dated evidence.
The README remains the short project overview; the documents below describe the
implemented system as it exists now.

## Current documentation

| Area | Source of truth |
|---|---|
| System architecture, events, states and artifacts | [`../DOCUMENTATION.md`](../DOCUMENTATION.md) |
| Installation and distributed deployment | [`../DEPLOYMENT.md`](../DEPLOYMENT.md) |
| Local workflow | [`../LOCAL_PIPELINE.md`](../LOCAL_PIPELINE.md) |
| Development, tests and dependency management | [`../DEVELOPMENT.md`](../DEVELOPMENT.md) |
| HD facade workflow | [`FACADE_ORTHOPHOTO.md`](FACADE_ORTHOPHOTO.md) |
| Fast aerial alignment and RTK/GCP behavior | [`FAST_ALIGNMENT.md`](FAST_ALIGNMENT.md) |
| Geospatial workspace and AI results | [`GEOSPATIAL_WORKSPACE.md`](GEOSPATIAL_WORKSPACE.md) |
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
- machine-readable Helenenschacht sparse/RTK evidence:
  [`benchmarks/helenenschacht-sparse-precision-ab-2026-07-31.json`](benchmarks/helenenschacht-sparse-precision-ab-2026-07-31.json)
- [`audits/`](audits/) and the date-stamped `AUDIT_*` / `CONTRE_AUDIT_*` reports
- [`GAJAN_R2S_VALIDATION.md`](GAJAN_R2S_VALIDATION.md)
- [`dronegs/CHANGELOG.md`](dronegs/CHANGELOG.md)

When historical evidence disagrees with current documentation or code, the
current executable contract and its tests take precedence.
