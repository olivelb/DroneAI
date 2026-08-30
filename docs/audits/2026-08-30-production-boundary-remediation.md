# Production boundary remediation — 2026-08-30

Baseline: `main` at `ec861136e79263993a8b4fffc5bbabf5ce0c8310`.

The work was performed while a separate BIGZEN E2E run was active. No
Playwright/E2E, physical GPU command, container build, Kubernetes apply,
Terraform apply or OVHcloud mutation was run. Repository checks ran only in
MSI WSL Ubuntu. The GPU workflow remains explicitly pinned to the MSI runner.

## Finding ledger

| Finding | Status | Repository evidence | Remaining target qualification |
| --- | --- | --- | --- |
| N-01 Kafka authentication/TLS | Fixed at application and chart boundaries | One validated client contract configures every producer/consumer; production and staging refuse unencrypted transport; Helm injects SASL credentials from an existing Secret and optionally mounts private CA/mTLS material. Production and preproduction examples use external `SASL_SSL`. | Exercise the selected external broker with real certificates, credentials, ACLs, rotation and outage recovery. |
| N-02 Stage identity comparison | Fixed | Read-only preflight parses PostgreSQL URLs, compares usernames rather than raw URLs, requires the same target DB, and rejects reuse with API/operator identities. Stage S3 access keys are compared with application and backup identities. | On the target DB, verify every role is `NOSUPERUSER`, `NOBYPASSRLS`, non-owner and limited to its stage grants. |
| N-03 unrestricted HTTPS egress | Fixed in portable chart contract | External HTTPS is restricted to explicit `networkPolicy.externalHttpsCidrs`; production refuses empty, placeholder and world-open CIDRs. | Replace the example CIDR with the stable egress proxy/gateway ranges and test S3/Hugging Face/observability flows. |
| N-04 hard-coded egress ports | Fixed | Database, S3 and Kafka egress ports are Helm values; production Kafka uses 9093 and Stage Jobs have no Kafka egress or environment. | Verify target service ports and firewall symmetry. |
| N-05 declarative Stage identities | Partially fixed | Terraform declares six independent OVH S3 users, credentials, least-privilege no-delete policies and sensitive output maps. | PostgreSQL is not managed by this Terraform stack; role creation/grants and Secret delivery still require an approved declarative owner. No Terraform apply was run. |
| N-06 independent review | External blocker | Critical paths remain covered by CODEOWNERS. | A second qualified maintainer and GitHub branch/environment required-review settings cannot be created by repository code. |
| N-07 mutable CI/service images | Fixed | CUDA, PostGIS, Kafka, MinIO and `mc` references used by CI/Compose/default Helm services are bound to resolved OCI index digests. | Renovate/update digests through reviewed PRs and re-run supply-chain scans. |
| N-08 Kubernetes 409 handling | Fixed | Job manifests carry a canonical template hash; on 409 the existing Job is fetched and its expected identity/spec is verified while tolerating server-added fields. Mismatch or a forged hash fails dispatch. | Exercise a real API-server replay during cluster qualification. |
| N-09 CSP reports discarded | Fixed | Bounded JSON reports emit structured sanitized events; URLs lose query/fragment data, non-HTTP locations are reduced to safe tokens and raw invalid bodies are not logged. | Route structured logs to the selected metric/log backend and alert on sustained violations. |
| N-10 mypy allowlist omission | Fixed | `control_worker_health.py` is now part of strict API mypy; the full typecheck passes. | None. |
| N-11 SAM3 artifact identity | Fixed at promotion contract | Protected Helm renders require an independent 64-hex artifact hash; the worker verifies `model.safetensors` before model deserialization. | Record the approved hash for the chosen revision in the external release values and perform one real download qualification. |
| N-12 CUDA memory safety | Implemented, execution pending | The MSI-only GPU job runs a short `compute-sanitizer --tool memcheck` pass over `dronegs_cuda_tests` after CTest. | Successful commit-scoped MSI GPU workflow evidence; intentionally not run while BIGZEN was busy. |
| N-13 CVE waiver scope | Fixed | Waivers now match CVE, image, package and installed version, plus owner/reason/expiry; incomplete Trivy package identity fails closed. | Add a waiver only after explicit security review of a real finding. |
| N-14 PlayCanvas patch debt | Accepted controlled debt | Exact dependency version, occurrence checks, idempotence and patch tests remain intact. | Replace with a reviewed patch artifact or minimal fork when upstream maintenance makes that lower risk. |

Additional hardening replaces the private Starlette `request._body` mutation
with a streaming ASGI receive wrapper and keeps request bodies readable by
downstream routes.

## Local evidence

- Python unit/component suite: `1691 passed, 1 skipped, 45 deselected` with
  `not gpu and not integration`.
- Python lint and strict mypy: passed, including the control-worker healthcheck.
- Frontend: ESLint and TypeScript passed; `44` Vitest files and `387` tests
  passed.
- Helm production/OVH contracts: `27` focused tests passed after secure overlay
  rendering; local default Helm rendering also passed.
- Stage orchestrator: `39` tests passed, including matching, mismatching and
  forged-hash 409 cases.
- Terraform: `13` contract tests, `fmt -check` and `validate` passed with the
  official Terraform 1.14.6 image and an isolated backend-free copy.
- CUDA script: ShellCheck and five CI contract tests passed; no GPU execution
  was performed locally.

These checks are repository evidence, not deployment, scientific, external
Kafka, GPU hardware or production qualification.
