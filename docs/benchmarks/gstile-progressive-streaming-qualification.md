# Progressive GSTile qualification — 2026-09-05

Local branch codex/windows-gstile-viewer, base a34009d, uncommitted changes.
Windows RTX 4070 Laptop 8 GiB; MSVC 19.38 and SDK 10.0.26100.9169.
Web: installed/patched PlayCanvas 2.21.4, WebGPU in Chromium.
Frontend and API changes are local; no production deployment was performed.

## Contract and test coverage

- 377 Python tests passed: gaussian_tiles suite, signed viewer API, attribute
  stream split/join and existing-bundle conversion.
- 333 GSTile frontend tests passed, followed by 33 focused tests including
  three additional signed-stream descriptor cases (336 distinct tests total).
- Frontend TypeScript, ESLint on changed implementation files and production
  Next build passed; Ruff on new Python modules passed; git diff --check passed.
- Windows Release shaders/program build and CTest passed.
- Real GPU contracts cover all colour/opacity SH degree combinations, sorting,
  picking, incremental arena allocation, partial upload retention, arena growth,
  unchanged-page reuse and a return to cached pages without new raw upload.
- Stream fixtures reject corrupt headers/CRC/SHA, bad ranges and signed identity
  mismatches. Canonical round-trip is byte-exact and base retains DC/logit/ID.
- Home camera serialization/validation is tested. Real Windows folder-picker,
  mouse gestures, fullscreen and menu interaction remain manual checks.
- The Windows GitHub workflow has not run remotely.

## Data and trajectories

The original Saint-Étienne bundle on Z: is unchanged: 6,373 nodes, 6,250 packs,
123,727,664 source gaussians, about 16.9 GB of canonical Q96.
ID: sha256:7ca65986f3a890bcd5e442db254febcdbbebca7d43a8b09a63bb205146f76821.

The split-stream qualification fixture copies the top 31 proxy nodes and
truncates the hierarchy at depth 4, preserving their actual Q96 records.
Its 16 terminal proxies contain 262,144 gaussians. This exercises real data
and several levels, but is not a full-resolution reconstruction.
ID: sha256:64f7f5e434675e1daf8e7d84936309badb2c6894431395c1f7c32e19a06ecab7.
At that initial v2 qualification the full bundle had not yet been converted.
The completed stream-only conversion is documented below.

Native streaming uses the production Loader/tick/arena, with orbit, pan and
dolly during 240 frames then 120 stationary frames, paced at 16 ms. A final
drain (up to 30 s) requires convergence, then verifies final GPU sorting.
GPU contracts run before the trajectory. CPU loop timings exclude pacing;
they are neither displayed frame intervals nor GPU durations. Background
loading continues concurrently. settledAfterMotionMs is the elapsed time
when final convergence is checked, an upper bound rather than the exact
first instant of convergence; samples/commits allow finer analysis.
Raw-upload counters exclude active-index uploads and derived GPU buffers.

Web streaming keeps the production selector/decoder/shaders and uses an
instrumented camera trajectory for 360 rAF frames then 360 stationary frames.
It runs over HTTP localhost with actual workers and GPU. It records frame
intervals to include asynchronous commit stalls that render-only timings miss.
Camera timing and transport differ from native: this is not an A/B comparison.
The debug snapshot's missingFromLoaded lists selected node IDs absent from the
legacy map because the actual resource is the single __merged__ arena; this
does not mean geometry is missing. baseOnlyNodes is empty at completion.

## Recorded v2 results

| Passage | Commits/changes | Final residents | First image | CPU loop/render p95 | Maximum |
|---|---:|---:|---:|---:|---:|
| Native split fixture, 250k budget | 20 | 245,760 | 27.91 ms | 0.864 ms | 11.88 ms |
| Native Saint-Étienne, 2M budget | 50 | 946,114 | 962.47 ms | 2.010 ms | 24.63 ms |
| WebGPU split fixture, 250k budget | 14 | 245,760 | 89.54 ms | 0.660 ms | 1.50 ms |

All three finish without missing SH. Both native reports explicitly converge.
WebGPU finishes budget-limited with no queued requests, GPU/Worker errors,
assembly failures or decoder fallbacks. Web frame interval p95 is 7.98 ms,
maximum 31.68 ms. Last local commit is 6.18 ms. GPU p95 is 1.18 ms.
Web transferred 33,031,616 bytes across 46 requests including prefetch.
These are single-run observations, not a speedup claim against Cesium or an
old streaming baseline. The native/WebGPU fixed-cut comparison in
native-viewer/QUALIFICATION.md is a separate rendering-only experiment.

The v2 browser run precedes a final diagnostic-only change that clears
loadingSh after completion and labels pending base-only idle state refining;
the production build/tests include those changes. Earlier v1 evidence is
retained. Native v2 precedes formatting only; final binary rebuilt/CTest passed.

## Reproduce

From the WSL repository, compile native-viewer/build.ps1 using the documented
portable SDK if needed, then invoke the Windows executable:

~~~text
GSTileViewer.exe --benchmark --streaming BUNDLE --budget 2000000 --frames 360 --output REPORT.json --screenshot CAPTURE.bmp
~~~

For WebGPU, run:

~~~bash
node native-viewer/benchmarks/prepare-streaming.mjs /path/to/harness
node native-viewer/benchmarks/serve-streaming.mjs /path/to/harness /path/to/bundle /path/to/native-report.json /path/to/web-report.json
~~~

The server is bound to 127.0.0.1:8770. Open its page and click the measurement
button. The bundled browser scenario uses a 250k budget. It expects a
browser with WebGPU; worker isolation headers are set by the local server.

Windows evidence is retained under Documents/DroneAI/GSTileViewer/evidence:
native-streaming-split-v2.json, native-streaming-saint-etienne-v2.json,
their BMP captures, webgpu-streaming-v2.json, and prior v1 reports.
The harness, server logs, original-derived fixture and build directories are
also retained. source-provenance-progressive-streaming.json identifies final
sources and binary; SHA256SUMS.txt identifies portable files.

## Remaining performance limits

Native raw uploads are sliced at 4 MiB but active-index publication is a full
list. Web copies only changed tiles into persistent textures, yet rebuilds
the derived PlayCanvas unified manager at each commit for correct interval
reconciliation. A first indivisible transition group may exceed the 65,536
target. A bounded active task finishes before selection catches up with the
latest camera, avoiding continual cancellation starvation. These choices do
not guarantee zero spikes on every GPU/tree/network.

The new transport streams are raw. Geometry/base is 36 B instead of 96 B;
SH adds 60 B later. Complete transfer is still 96 B. The initial additive
prototype retained compatibility copies; the current stream-only producer
removes them, as documented below. No whole-bundle compression gain is claimed. Compression of
the separate streams, finer pack sizing and removal of the derived WebGPU
rebuild are future work, requiring independent measurements and compatibility
qualification. Existing bundles need conversion or rebundling for base-first
transport; the LOD/arena changes also apply to their canonical packs.

## Completed stream-only conversion — 2026-09-05

The new producer writes only .gst.base/.gst.sh and its manifest, with explicit
storage: streams. It no longer compresses or writes historical .gst/.zst.
The API signs only real stream files; both updated viewers decode this output.
Virtual Q96 metadata and its header remain in the manifest for exact validation.
Older bundles remain readable by the new viewers. The web/API/bundler changes
are in the local source checkout; no production deployment was performed.

Converted next to the original:
Z:/droneai/saint-etienne-native-products-20260903/gstile-streams.
New ID: sha256:398f7b4e16d599eb546419671c9f28cd568d956d2936bc483e9a12e88d35edf1.
6250 aggregate groups, 12500 stream files,
plus manifest.json. Historical files in output: none. The source LOD tree,
quantization, pack identities and source metadata are unchanged.
The converter checks source SHA/CRC, flushes outputs and reads back their SHA.
A resumed run reverified and reused existing completed files. Each output
length and the complete file inventory were verified after publication.
Source manifest matches the prior local copy: True.

| Actual directory storage, including manifest | Bytes | Decimal GB |
|---|---:|---:|
| Original, with historical raw and compressed files | 29636065393 | 29.636 |
| New, only attribute streams | 16907371319 | 16.907 |

This removes duplicate storage; the full 36+60-byte attribute payload is still
96 bytes per Gaussian. It does not compress the SH stream or reduce quality.

Fixed-camera/cut qualification compared original and converted bundles on the
same native renderer: identical framebuffer bytes = True.
Both passes independently verify the GPU sorting/SH contracts and decode the
selected packs. The stream-only native moving-LOD run converges with
1985926 residents and zero base-only pages remaining.
Its CPU-loop p95 is 2.381 ms; maximum 23.480 ms.
The native CPU distribution covers the scripted 360 frames; the final drain
verifies completion but is not part of that distribution.
Loading timings are single runs influenced by OS/SMB caches and are not treated
as an A/B speedup measurement.

The WebGPU stream-only fixture also completed its actual GPU/worker trajectory:
zero errors, all SH present, 245,760 residents. Frame interval p95
7.345 ms, maximum 32.410 ms.
Full converted bundle streaming on WebGPU also converged:
1999936 residents, all SH present,
zero errors, 103 observed cut/quality changes. Frame
interval p95 13.140 ms; maximum
191.110 ms. This includes asynchronous work and
still shows occasional stalls; it is not evidence of uniformly smooth frames.

The complete dataset exposed a native result-publication race and target
feedback through intermediate cuts. Native busy state now includes unpublished
results, and both viewers retain a stable target until the camera/viewport or
budget changes. The native async-loader test covers a pending result and SH
completion. Early failed native reports are retained but are not accepted
convergence evidence; v3 and the full WebGPU run are the accepted passes.
Python: 379 affected-suite tests plus six new metadata rejection cases passed;
frontend: 337 GSTile tests, typecheck, lint and production build passed.
Windows Release/CTest and actual GPU fixture/full-bundle runs passed.

Evidence: saint-etienne-streams-conversion.json, comparison-canonical-only.json,
comparison-streams-only.json, their BMP captures, comparison-streams-pixels.json,
native-streams-only-saint-etienne-v3.json, webgpu-streams-only.json,
webgpu-streams-only-saint-etienne.json, converter logs.
All prior evidence remains retained.

## Balanced close-up LOD selection — 2026-09-05

The native selector now uses a single budget-adjusted screen-error threshold
across the view, matching the web selector's existing fairness policy.
It does not refine cheap, lower-priority neighbours after a coarser branch
runs out of budget. Equal-error branches refine together. Projected error is
bounded when the camera enters conservative Gaussian support.
Both selectors now cull support AABBs against the five frustum planes rather
than their circumscribed spheres; offscreen centers with visible Gaussian
support remain eligible. Streaming keeps its stable target and atomic cuts.
Native window status distinguishes an intermediate cut from a final budget limit.

Two reproducible 1440x900 close-ups were derived from the saved home camera.
They are near the reported sculpture, not a reconstruction of the screenshot's
unknown exact camera. Tests use the full converted Saint-Etienne bundle,
RTX 4070 Laptop, SH3, and explicitly saved camera JSONs. 60 measured frames
follow 32 warmup frames, with projection/sort every frame.

| Capture/report | Resident splats | Max calculated LOD error (px) | GPU median (ms) |
|---|---:|---:|---:|
| lod-close-baseline | 1991437 | 543.12 | 6.64 |
| lod-close-corrected | 1971106 | 436.32 | 6.89 |
| lod-carving-baseline | 1967528 | 736.07 | 7.21 |
| lod-carving-corrected | 1982126 | 543.30 | 7.72 |
| lod-carving-corrected-4m | 3968402 | 283.85 | 15.29 |

The images show improved carving detail at the same 2M budget but still retain
blurred proxies in some regions. A separate 4M run gives much more detailed
lower carvings at increased GPU cost. The default remains 2M; this is a selection
quality correction, not a GPU speedup claim or a guarantee of exact leaves
everywhere. Reported error is an LOD heuristic, not measured image quality.
A coherent cut can leave budget unused when the next refinement group cannot fit.

The 360-frame moving-LOD test starts from lod-carving-camera.json and verifies
actual selected nodes equal target nodes, zero missing SH pages, and budget
compliance. It converged: 1580033 residents, settle time
7886.45 ms after motion, CPU loop p95
2.718 ms (scripted frames only; completion drain excluded).
The nonzero settle time means intermediate coarse content can still be visible
while network reads and uploads complete.

Validation: Windows Release build/CTest (including seven new LOD checks),
native GPU sorting/SH contracts and the full-bundle captures/streaming run;
338 GSTile frontend tests, TypeScript and focused ESLint passed.
The WebGPU culling change was covered by CPU contracts/typecheck; a new browser
GPU run was not performed for this change. No deployment, commit or bundle
rewrite was performed. Existing conversion/qualification evidence is retained.

Evidence: lod-close-*.json/.bmp, lod-carving-*.json/.bmp,
lod-balanced-streaming.json, source-provenance-balanced-lod.json.
The baseline executable with explicit benchmark camera input is retained in
the adjacent GSTileViewer-lod-baseline directory.

Final exact-cut guard: both selectors first check the full visible leaf cut.
Culling can make that cut cheaper than intermediate proxies, invalidating a
monotone-cost assumption. A new regression in each viewer verifies this case.
The 338-test frontend suite plus the final focused 20-test selector suite
(339 distinct GSTile cases across runs), typecheck and focused lint passed.
Final Windows Release/CTest passed. The final GPU streaming run is
lod-balanced-streaming-v2.json: 1580033 residents, exact target match,
zero missing SH pages, 8247.45 ms settle time and
2.359 ms scripted-loop CPU p95. Earlier captures and reports remain
available; the final binary identity is in source-provenance-balanced-lod.json.
