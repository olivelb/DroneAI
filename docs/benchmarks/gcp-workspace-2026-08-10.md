# GCP workspace implementation qualification — 10 August 2026

## Scope

This record qualifies the software path introduced for professional map
editing and ground-control preparation. It covers cursor/tool semantics,
editable WGS84 coordinates, durable multi-format GCP import, hybrid calibrated
camera/EXIF candidate selection, bounded precision image marking, checkpoint
roles, immutable calculation bundles, append-only audit and
reconstruction-stage validation.

It does not claim photogrammetric accuracy. That requires a real surveyed
dataset, independent checkpoints and a complete GPU reconstruction on BIGZEN
or the target cloud environment.

## Automated evidence

The focused Python suite covers Metashape XML, KML, Leica/Trimble/custom CSV
mapping, CRS conversion, portable COLMAP camera projection, EXIF fallback,
non-destructive refresh, server-side pixel bounds, persistence schemas and
constraints, append-only event construction, deterministic bundle construction,
minimum observation gates, content identity verification, worker
download/reuse and reconstruction-only stage binding.

The frontend unit and Playwright suites cover tool cursors, selection and
deselection, editable coordinates, GCP import/editing, candidate refresh,
bundle validation and native-pixel image marking. The precision editor adds a
loupe, projected seed and keyboard nudging; the set history keeps expandable
before/after evidence. The production Next.js build and bilingual catalogue
are also checked.

The final CPU-equivalent CI run completed with 826 tests passed and 14
GPU/integration tests deliberately deselected. The zero-duplication gate,
modular boundary checks, frontend build and all 10 Playwright scenarios passed.
The PostgreSQL/PostGIS migration was also upgraded to `0021`, downgraded to
base and re-upgraded. Direct audit-row update/delete attempts were rejected by
the database trigger, while deletion of the parent GCP set retained its normal
cascade semantics.

Commands used from the authoritative Ubuntu WSL checkout:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_gcp_candidates.py tests/test_gcp_bundle.py \
  tests/test_gcp_control.py tests/test_gcp_import.py \
  tests/test_gcp_workspace.py tests/test_gcp_audit.py \
  tests/test_camera_projection.py tests/test_database_state_constraints.py \
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
