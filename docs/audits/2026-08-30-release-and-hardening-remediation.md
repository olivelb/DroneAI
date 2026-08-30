# Release and hardening remediation — 2026-08-30

Baseline: `main` at `3a45961cd4f1df16417e4318a7ec11a109233d9f`.

This remediation was implemented while a separate BIGZEN E2E qualification was
running. No GPU/CUDA test, Playwright E2E, container build, Kubernetes/Helm
apply, Terraform apply or OVHcloud mutation was performed.

## Finding ledger

| Finding | Status | Repository evidence | Remaining qualification |
| --- | --- | --- | --- |
| F-01 promotion shell corruption | Fixed | Repeated scan/sign logic moved to `scripts/ci/promote_image.sh`; no literal `+` separators remain; shell and workflow lint pass. | First real signed tag promotion. |
| F-02 skipped physical GPU accepted | Fixed | `verify_release_qualification.py` requires exact SHA, `workflow_dispatch`, successful `cuda-tests` and the non-expired SHA-bound artifact. | Manually dispatched run on the release commit. |
| F-03 OVH bootstrap drift | Partially fixed | Implicit bootstrap removed and retired; read-only preflight verifies ten Secrets, every required key, auth lengths and distinct Stage DB/S3 identities; every deployment requires a complete release values file. | Identities still need an approved password-manager/external-secrets or Terraform design; no OVH deployment was attempted. |
| F-04 unsafe production Kafka | Fixed in chart contract | Production disables the in-chart broker and requires an explicit external broker; production overlay uses Traefik. | Qualify the selected external Kafka service, TLS/auth and failure drills. |
| F-05 text-only infra/supply tests | Improved | Qualification, Secret and CVE decisions are executable Python with behavioral tests; Helm is rendered in tests. | Continue converting older textual deployment tests as those paths change. |
| F-06 critical ownership/reviews | Partially fixed | `.github/CODEOWNERS` covers workflows, CI scripts, identity/security, migrations, charts, infra and waivers. | GitHub branch/environment review policy must be configured by repository administrators. |
| F-07 native security coverage | Fixed at repository gate level | Hosted CPU native job enables ASan/UBSan; CodeQL now selects C/C++ (including CUDA sources) in addition to Python and JS/TS. | Run CodeQL on GitHub and add short physical `compute-sanitizer` cases after the active BIGZEN run. |
| F-08 CSP report-only | Fixed | Next 16 `proxy.ts` emits a per-request nonce CSP with `strict-dynamic`; bounded report endpoint and unit/E2E assertions added. | Playwright was intentionally not run during BIGZEN qualification. |
| F-09 unrestricted HTTPS egress | Deferred | Portable NetworkPolicy limitation remains documented. | Select an egress proxy/gateway, Cilium FQDN policy or stable target CIDRs for the actual production network before deployment. |
| F-10 unfixed CVEs implicitly accepted | Fixed | Promotion rejects unwaived unfixed HIGH/CRITICAL findings; waivers require image, owner, reason and non-expired date. | Populate only after explicit security review when a real scan requires it. |
| F-11 alerts/probes | Fixed in chart and worker | Production alerts are mandatory; control worker writes an atomic leadership/loop heartbeat and has liveness/readiness probes, with readiness also querying PostgreSQL. | Alert routing and disruption drills on the target cluster. |
| F-12 session rotation | Fixed | Session tokens carry a bounded `kid`; current plus previous keys allow overlap, and legacy no-`kid` tokens can migrate through the bounded ring. | Operator rotation drill after deployment. |
| F-13 PlayCanvas patching | Accepted controlled debt | Exact version, occurrence checks, idempotence and failure behavior remain covered. | Replace with a reviewed patch artifact or minimal fork when upstream maintenance justifies it. |

Additional hardening: the API now rejects bodies over the configured global
limit before FastAPI model/form parsing. The Stage Job 409 identity comparison
and a provider-specific egress design remain defense-in-depth follow-ups.

## Local evidence

- Python: `1665 passed, 1 skipped, 45 deselected` with `not gpu and not integration`.
- Frontend: `44` Vitest files and `384` tests passed; TypeScript and ESLint passed.
- Helm: production contract renders passed (`14`, then `24` focused tests after probes).
- Workflows: `actionlint` passed for CI, CodeQL and signed promotion.
- Python lint: Ruff passed for all changed Python files.

These are repository/component checks, not target-environment, GPU, scientific
or production-release qualification.
