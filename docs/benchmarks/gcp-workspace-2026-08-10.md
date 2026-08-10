# GCP workspace implementation qualification — 10 August 2026

## Scope

This record qualifies the software path introduced for professional map
editing and ground-control preparation. It covers cursor/tool semantics,
editable WGS84 coordinates, durable GCP import, EXIF-distance candidate
selection, native-image marking, checkpoint roles, immutable calculation
bundles and reconstruction-stage validation.

It does not claim photogrammetric accuracy. That requires a real surveyed
dataset, independent checkpoints and a complete GPU reconstruction on BIGZEN
or the target cloud environment.

## Automated evidence

The focused Python suite covers the supported import formats, CRS conversion,
candidate ranking and non-destructive refresh, persistence schemas and
constraints, deterministic bundle construction, minimum observation gates,
content identity verification, worker download/reuse and reconstruction-only
stage binding.

The frontend unit and Playwright suites cover tool cursors, selection and
deselection, editable coordinates, GCP import/editing, candidate refresh,
bundle validation and native-pixel image marking. The production Next.js build
and bilingual catalogue are also checked.

Commands used from the authoritative Ubuntu WSL checkout:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_gcp_candidates.py tests/test_gcp_bundle.py \
  tests/test_gcp_control.py tests/test_gcp_import.py \
  tests/test_gcp_workspace.py tests/test_database_state_constraints.py \
  tests/test_phase_dag.py

make PYTHON=.venv/bin/python lint shared-lint service-core-lint typecheck docs-check

cd app4-dashboard/frontend
npm test
npm run lint
npm run build
npm run test:e2e
```

CUDA, COLMAP and DroneGS rebuilds are intentionally excluded: no native source,
CUDA version, GPU architecture, CTest definition or pinned COLMAP dependency is
changed by this workspace feature.

## Remaining real-data gate

Before production use, perform one complete reconstruction from a materialised
GCP bundle on BIGZEN, verify that adjustment points affect the solution, and
publish independent checkpoint horizontal/vertical residuals. Repeat that gate
on OVHcloud when a compatible GPU quota becomes available; BIGZEN evidence
does not qualify a different GPU target.
