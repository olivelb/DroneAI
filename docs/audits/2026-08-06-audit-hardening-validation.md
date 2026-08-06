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

## Remaining release checks

These focused checks do not replace the full `make check`, a complete
Compose/K3s mission, Helm acceptance, RTK/non-RTK dataset regressions or a
centrally retained GPU workflow artifact. Run the remaining repository release
gates before publication.
