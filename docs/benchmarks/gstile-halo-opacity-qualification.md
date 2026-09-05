# GSTile halo cache and opacity qualification — 2026-09-05

Authoritative checkout: /home/olivier/droneAI, branch codex/windows-gstile-viewer,
uncommitted changes. Portable Windows x64 MSVC Release, RTX 4070 Laptop 8 GB.
Full stream-only Saint-Etienne bundle:
sha256:398f7b4e16d599eb546419671c9f28cd568d956d2936bc483e9a12e88d35edf1.
No bundle files were changed and no web deployment was performed.

## Native memory/cache behavior

Decoded-page and raw-pack LRUs: 768 MiB each, excluding active owners and in-flight
work. Active verified pages are reused directly even beyond the optional LRU.
GPU raw arena retains inactive pages by last use: extra reserve is at most 4M
records / 400 MB and at most VRAM/16, also capped by twice the visible budget.
It retains its largest allocation during the session. Work/sort buffers still
use the visible budget; prefetched pages are neither projected nor drawn.

After foreground geometry and requested SH finish and the camera is stable for
350 ms, prefetch selects a 1.5x-FOV halo capped at 120 degrees (never narrows an
already wider FOV). Virtual viewport scaling preserves focal pixel density.
The plan is capped at min(visible budget, half the reserve), hence up to 200 MB.
Reads/uploads proceed in groups up to 65,536 splats; an indivisible larger tile
can exceed that group limit. Movement cancels queued/in-progress work at its next
cancellation check and invalidates incomplete GPU pages. A blocking filesystem
read already started must finish before cancellation can be observed.
Visible work always has priority. Complete RAM+VRAM target cuts activate directly
without intermediate proxies, payload reads or payload uploads.

## Real A → B → A test

--benchmark --cache-cycle --camera lod-carving-camera.json.
B translates laterally by 0.35 times camera-to-pivot distance. Halo-enabled
runs wait for halo preparation before moving. Disabled runs retain the same
history cache; the toggle controls speculation, not existing cached history.
The timestamp is foreground completion, not a pure GPU frame time.
OS/SMB caches and thermals were not cleared; these are single runs.
File bytes count read calls reaching the filesystem, not physical network I/O.
The last view may select a different valid cut due to hysteresis.

| Mode | Phase | Ready ms | File read bytes | Raw GPU upload bytes |
|---|---|---:|---:|---:|
| 2M halo off | A | 13922.41 | 271936980 | 606140400 |
| 2M halo off | B | 877.70 | 16984992 | 33336400 |
| 2M halo off | A-return | 36.02 | 0 | 0 |
| 2M halo on | A | 12681.11 | 271936980 | 606140400 |
| 2M halo on | B | 493.00 | 8061216 | 22937600 |
| 2M halo on | A-return | 32.22 | 0 | 0 |
| 4M halo on | A | 23287.12 | 507451500 | 1127914000 |
| 4M halo on | B | 1222.11 | 23219776 | 53288400 |
| 4M halo on | A-return | 39.61 | 0 | 0 |

At 2M, B used 16.98 MB without halo vs
8.06 MB with halo, and completed in
877.70 vs 493.00 ms.
The final warm return used zero payload reads/uploads, taking
32.22 ms at 2M and
39.61 ms at 4M.
This does not guarantee instant arbitrary rotations or cold camera jumps.
Earlier off/on reports are retained as intermediate implementation evidence;
the v2 and on-4m reports qualify the final executable.

## WebGPU opacity

The previous raster used tail-subtracted/renormalized normExp and sqrt(8)-sigma
support. The GSTile device now uses exp(-r²/2) and 3-sigma quad/fragment support,
matching the native kernel. This preserves premultiplied alpha, SH logits,
depth sorting and existing compensated-AA=false. It is not an opaque rendering
workaround and does not modify the bundle.
Native and WebGPU retain different projector clamps/precision/sort details;
pixel identity is not claimed.

prepare-transparency.mjs / transparency.mjs generate the production-renderer
harness. serve-streaming.mjs serves the bundle and lod-carving-corrected.json.
The fixed native cut has 1,982,126 splats at 1440x900, identical camera and SH3.
The harness waits for all target nodes and SH before recording its canvas.
Both real WebGPU runs have zero errors and matching resident populations.

Pixel errors are RGB 0..255 against lod-carving-corrected.bmp, no image edits.
Dark mask: native mean RGB < 80 (366330 pixels), fixed for both.

| Metric | Old WebGPU kernel | Native-compatible kernel |
|---|---:|---:|
| Full-image RGB MAE | 3.3021 | 2.0012 |
| Dark-mask RGB MAE | 2.5423 | 1.2896 |
| Full-image RGB RMSE | 5.0916 | 3.6202 |

The dark-region mean error falls about
49.3% on this view.
This is an image-agreement metric, not a universal transparency accuracy bound.
The current pre-fix renderer was already visually close to native; the user's
older hosted build and every reported dark region were not individually reproduced.

Web halo cache accounting additionally uses base-stream hashes/lengths rather
than virtual Q96 identities, with canonical fallback for older signed descriptors.
This improves transport/RAM reuse; inactive WebGPU resources are not newly cached
in VRAM by this change.

## Validation and evidence

Windows Release/CTest passed. Real GPU contracts verify invisible prefetch,
invalidation of a partly uploaded page, fully cached activation and valid sort.
CPU tests verify active-page reuse with an empty LRU and cache-only misses/hits.
Final 2M off/on and 4M on runs verify convergence, zero halo errors, correct GPU
sort/SH contracts and zero-payload warm returns.
Frontend: 342 GSTile tests, typecheck, focused ESLint and production Next build
passed. Browser raster qualification covers the alpha change; the later transport
fallback correction has type/build/lint and selector/transport test evidence.

Reports/captures: native-halo-cycle-off-v2.json, native-halo-cycle-on-v2.json,
native-halo-cycle-on-4m.json, webgpu-transparency-baseline.json/.png,
webgpu-transparency-corrected.json/.png, webgpu-transparency-pixels.json.
The source provenance and executable SHA are source-provenance-halo-opacity.json.
Benchmarks and portable ZIPs from prior deliveries remain retained.
One prior raw report, lod-balanced-streaming-v2.json, was accidentally overwritten
by a streaming smoke rerun before this final cache qualification; its earlier
numbers remain in the previous written qualification, not in that raw file.
Use the distinctly named final halo-cycle reports for this implementation.
