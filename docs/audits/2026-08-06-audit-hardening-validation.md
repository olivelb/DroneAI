# Audit hardening validation — 2026-08-06

## Scope

This note records the changes and local checks performed in response to the
2026-08-04 DroneAI audit. The focused Python, frontend and documentation checks
ran before commit; the physical CUDA qualification was then repeated against
clean commit `1eeb49ef501482b9e745036a0ef557348c53e922`. It is validation
evidence, not a deployment record.

## Implemented changes

- The COLMAP image and both supported orchestrators run the worker as fixed
  UID/GID `10001`, with a read-only root filesystem, dropped capabilities and
  explicit writable workspace and temporary mounts. Deployment scripts create
  the primary workspace with mode `0770` and the required ownership.
- The GPU workflow retains a commit-scoped validation log for 30 days and adds
  its commit, runner and result to the GitHub job summary. The shared validation
  script reports its source commit, Docker server and CUDA image contracts.
- Playwright exercises the production Next.js application in Chromium with
  mocked API transport. The covered journeys select and launch a dataset,
  cancel a running mission and render cancellation as a terminal state.
- `cancelled` is now a first-class shared event and mission status instead of
  being represented as `error`. The API, resume policy and frontend use the
  same four-state contract: `processing`, `success`, `error`, `cancelled`.
- Workspace deletion no longer uses silent error suppression. Cleanup reports
  a verified result, exposes structured success/failure information and cannot
  overwrite the final mission status.
- Transactional inbox/outbox edge cases received focused regression tests for
  duplicate events, incomplete receipts, Kafka message locations, retries,
  dead letters and stolen dispatcher claims.

## Verification results

| Verification | Result |
|---|---|
| Local Markdown link check | Passed |
| Targeted Python regression suite | 65 passed |
| Frontend Vitest suite | 8 passed across 2 files |
| Frontend ESLint | Passed |
| Next.js 16.2.12 production build | Passed |
| Playwright Chromium mission journeys | 3 passed |
| Native CUDA/DroneGS CTest suites | 6 passed, 0 failed |
| CUDA production-runtime driver injection | Passed in runtime and cuDNN runtime images |

The targeted Python command covered COLMAP publication and cleanup, support
modules, API architecture, CUDA CI contracts, shared event validation, GPU
dependency contracts and transactional inbox/outbox behavior:

```bash
.venv/bin/python -m pytest -q \
  app1-colmap/test_colmap_worker_publication.py \
  app1-colmap/test_support_modules.py \
  tests/test_api_architecture.py \
  tests/test_cuda_ci_contract.py \
  tests/test_event_contracts.py \
  tests/test_gpu_dependency_contract.py \
  tests/test_inbox_outbox.py
```

Frontend verification used:

```bash
cd app4-dashboard/frontend
npm run test
npm run lint
npm run build
npm run test:e2e
```

The physical-GPU environment and individual native results are recorded in
[`../benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md`](../benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md).

## Follow-up upgrade validation

The follow-up work based on commit `76c37fd` closed the remaining strict-typing
gap and extended supply-chain evidence to the final CUDA images:

- strict mypy now passes across all 26 root `shared/` modules, including the
  SQLAlchemy, inbox/outbox and boto3 boundaries;
- `cuda-containers.yml` prepares checksum-verified external sources, builds the
  final COLMAP base and local Gaussian runtimes, and applies the same pinned
  Syft/Trivy evidence and fixable-CRITICAL gate as the standard service images;
- the Gaussian runtime explicitly refreshes OpenSSL packages inherited from
  the NVIDIA base. The first scan found two HIGH records for
  `CVE-2026-45447`; rebuilding with Ubuntu's fixed OpenSSL packages reduced the
  HIGH/CRITICAL report to zero.

Local follow-up results:

| Verification | Result |
|---|---|
| Complete non-GPU Python suite | 381 passed, 13 deselected |
| Repository static checks | Passed, including strict mypy on 26 shared modules |
| Targeted database/storage regressions | 27 passed |
| Final local Gaussian CUDA image build | Passed |
| Syft CycloneDX inventory | Passed, 4,941 components |
| Trivy HIGH/CRITICAL report after OpenSSL refresh | 0 findings |
| Trivy fixable-CRITICAL gate | Passed |

The full COLMAP base build advanced through the multi-architecture Caspar and
COLMAP CUDA compilation but exceeded the local command's 20-minute execution
window before image export. The hosted matrix has a 90-minute timeout and is
the authoritative completion and scan evidence for that larger image.

The next local hardening pass extended the worker-grade Ruff rules to all
`shared/` modules. Bugbear, simplification, Python upgrade, Ruff-specific and
async checks now run in `make static`; shared complexity starts at a blocking
McCabe ceiling of 18 while the worker remains at 15. Intentional scientific
Unicode in operator-facing validation messages is the only scoped rule
exception. The complete `make check` gate passed after the migration: 381
tests passed, 13 GPU/integration tests were deselected, coverage remained 54%,
and `pip-audit --strict` reported no known vulnerabilities.

## Remaining release checks

The full local `make check` now passes. It does not replace a complete
Compose/K3s mission, Helm acceptance, RTK/non-RTK dataset regressions or a
centrally retained GPU workflow artifact. Run those environment-dependent
release gates before publication.

Pull-request CI is subsequently path-scoped: each long-running job starts only
for changes to its application, runtime, dependency lock or deployment
contract. Documentation-only changes receive a dedicated link check. The full
matrix remains available by manual dispatch and runs on a PR whenever the CI
workflow or scope selector itself changes. A merge to protected `main` does not
repeat already successful PR checks. An always-present aggregate gate converts
the conditional job results into one stable required status.

The CUDA workflow is more restrictive than the general path-scoped CI. Pull
requests perform only a lightweight diff classification for CUDA source,
Dockerfile or workflow changes. The 45/90-minute CUDA and COLMAP jobs run only
after an authoritative NVIDIA CUDA base-image line or the pinned `COLMAP_TAG`
changes, or after an explicit manual dispatch. A merge does not trigger the
workflow at all. A lightweight CUDA gate is still reported on every PR, which
allows branch protection to require the selector outcome while costly builds
remain skipped. The final local gate passed with 396 tests, 13 GPU/integration
tests deselected, 54% coverage and no known vulnerability in the locked Python
environment.
