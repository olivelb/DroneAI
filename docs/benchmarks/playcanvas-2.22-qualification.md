# PlayCanvas 2.22 qualification

Date: 2026-09-08. Baseline 2.21.4; candidate 2.22.0, PR #333.
The five local patches remain necessary. Their version guards now admit only
2.22.0; source-anchor and partial-patch checks remain fail-closed. The npm 10.8.2
lock repair restores the required brace-expansion/balanced-match resolution.

## Patch review

| Patch | Upstream comparison and disposition |
|---|---|
| FastGS off-axis covariance | Projection shader and clamp anchor unchanged; retain the patch across all eleven declared artifacts. |
| Float32 work-buffer transforms | Packed read/write shader bodies unchanged. GSplatParams adds LOD/tonemap/depth options, but the patched stream layout is unchanged; retain three production ESM patches. |
| Incremental world updates | Upstream changes octree budget/LOD selection. The placement-churn full-rebuild block and allocation-diff contract remain; retain the incremental patch. |
| Stable container intervals | Bounds, world-state offsets and work-buffer upload code retain the reviewed anchors. Keep the component API, declarations and three-build corrections. |
| Shared work-buffer view direction | WGSL introduces a local modelView alias without changing its expression. Retain the direction helper/conditions and stable pass names in GLSL/WGSL and all three builds. |

GSTile uses procedural non-octree containers and its own selection budget. This
upgrade does not opt into upstream octree error-based LOD defaults or change
DroneAI's production parameters. Clean installation applies every patch;
a second application changes zero artifacts. All 4,924 candidate provenance
hashes match the installed patched files (baseline: 4,832). The clean reinstall
reproduced the hashes of the artifacts used for browser qualification.

## Validation and limits

- 437 Vitest tests, frontend lint/typecheck and production Next build passed.
- Fourteen existing mission-lifecycle/security E2E tests passed. npm audit: zero.
- Actual browser: Windows HeadlessChrome 152, NVIDIA Lovelace WebGPU, fallback
  false; local GPU is RTX 4070 Laptop. Fixtures were served read-only on loopback,
  with every pack/stream SHA256 checked before serving.
- Real V4 fixture: 262,144 gaussians, color SH3, 31 packs, bundle
  `sha256:64f7f5e434675e1daf8e7d84936309badb2c6894431395c1f7c32e19a06ecab7`.
  Default assembly was captured twice per engine, then main-thread assembly once
  per engine. Five views: initial, pan, zoom, edge opacity 35%, changed FOV.
- Directional-opacity control: the existing benchmark generator produced 16,384
  records with color SH3 and opacity SH3, leaf size 4,096, proxy size 1,024 and
  one pack worker. Source SHA256
  `b747244929dc2e01dbc5d49800f1b1db8ba03c09ead7bf5c467860c1e75d96b9`.
  Both engines loaded the current split base/SH transport; six views include
  camera rotation. This is a functional synthetic control, not training evidence.
- All 42 final captures had visible scene pixels, ready state, resident=target,
  matching node/gaussian counts between engines, no pending nodes, no decode
  Worker fallback and no browser errors in the completed fixture harness.

The first strict pixel comparison failed: reloads of the baseline itself also
have sparse differences. Baseline self-reload mean absolute RGB difference was
0.0558–0.1298 on the 0–255 scale, p99 1–3, maximum 71–79. Candidate self-reloads
show comparable variation. Two additional frozen-frame comparisons were exactly
identical, locating the variation in reloads rather than continuous flicker.
This evidence does not establish the precise cause of the reload differences.

Before fresh main-thread and directional captures, the protocol admitted mean
absolute difference <=0.25, p99 <=4, maximum <=100 and at most 0.02% of pixels
with any channel difference >32. The region excludes the changing top 160-pixel
HUD and a 16-pixel border; retained screenshots are unmodified. All eleven fresh
paired views pass. Main-thread real-scene MAE is 0.0494–0.1128, p99 <=3; directional
MAE <=0.00213, p99 0 and maximum <=6. This is bounded visual compatibility, not
pixel-exact equality, a speedup or a full-scene/multi-hardware quality claim.

The initial failures and repeatability results are retained alongside the final
[structured results](playcanvas-2.22-results.json), including screenshot hashes,
GPU/browser identity, per-view counts, patch hashes and provenance hashes.
Raw captures and the local CLI drivers are retained under the task's
`output/playwright/playcanvas-20260908` and `droneAI-api-viewer-evidence/playcanvas`
evidence directories. Harness-only setup failures (CSP due to a non-loopback
listener and incomplete API mocks) were corrected before accepted captures;
no production authentication or security policy was weakened.

## Reproduction and rollback

Use npm 10.8.2: clean install, run postinstall again to check idempotence, run
`npm test`, `npm run lint`, `npm run typecheck`, `npm run build`, then the existing
mission E2E suite. Verify every generated playcanvas-provenance hash against
node_modules. Use a real hardware WebGPU adapter and identical fixtures/camera
inputs for the visual comparison; never count a software adapter or a blank
canvas as qualification. Regenerate the small SH3 fixture with the checked-in
`tools/benchmark_gstile_tiler.py` generator and `tools/build_gstiles.py` options
above. Main-thread assembly uses `gstileWorkerAssembly=0`.

Merge requires all selected CI on the final candidate. Roll back package.json,
the lockfile and all five strict patch version guards together to 2.21.4, then
rebuild the provenance and frontend. No release tag or deployment is part of this
qualification.
