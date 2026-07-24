# DroneGS GPL and third-party provenance register

Policy: GPL components are allowed. Their provenance and redistribution
obligations remain explicit at file, binary, container, and documentation
level. This is an engineering inventory, not legal advice.

## Current components

| Component | Revision/source | License | Local use | Treatment |
|---|---|---|---|---|
| LichtFeld-Studio | `MrNeRF/LichtFeld-Studio` at `1004c0841a3776e3f67866ff34101fbc9677397f` | GPL-3.0-or-later | Ignored external source and trainer binary | Keep license, exact source, notices, build scripts, and modification record available |
| LichtFeld minimal patches | `app1-colmap/patches/lichtfeld-*.patch` | Modifications to GPL work | Headless/minimal build changes | Treat patches and resulting binary as GPL-covered modifications |
| LichtFeld runtime image | `Dockerfile.lichtfeld` and LichtFeld stage in `Dockerfile.base` | Contains GPL binary | Subprocess runtime | Bundle notices and ensure corresponding source is obtainable |
| LichtFeld Python adapters | `gaussian_ortho/lichtfeld_trainer.py`, `gaussian_training/backends.py` | Original DroneAI code; process boundary | CLI adapters and PLY consumer | Keep process boundary; do not copy GPL implementation into wrappers |
| DroneGS native trainer | `app1-colmap/dronegs/` | MIT; original DroneAI code | Fixed-topology CUDA training prototype | No LichtFeld source copied; Phase 4 JPEG, cache, projection, rasterizer, backward, and Adam files are original; record any future adapted file separately before import |
| gsplat | Official `nerfstudio-project/gsplat`; revision not selected | Apache-2.0 | Candidate future rasterizer source | Import from official source, pin revision, retain notices, list adapted files |
| INRIA 3DGS code | Reference implementation; revision not selected | Custom research license | Algorithmic reference only | Do not copy source until separately approved |
| ImprovedGS+ | Revision and license not audited | To be determined | Post-parity experiment candidate | No source import until audited |
| NVIDIA CUDA/images | NVIDIA distributions | NVIDIA terms | Build/runtime | Preserve redistribution notices and terms |
| COLMAP | Pinned by DroneAI | BSD-3-Clause | Reconstruction/input format | Preserve license and attribution |

## File-level rules

Source copied or adapted from a GPL project must:

1. carry the correct SPDX identifier;
2. identify the upstream project and exact revision;
3. state that it was modified and give the date;
4. be listed here with its local path;
5. remain in a component distributed consistently with the GPL;
6. include the material needed to reproduce the binary.

Original DroneGS files implementing published algorithms without copying source
carry the selected project SPDX identifier and must not claim an inapplicable
upstream copyright.

## Release checklist

- [ ] Exact source revisions are pinned.
- [ ] Licenses and third-party notices ship with each relevant image.
- [ ] Modified GPL files and patches are identified.
- [ ] Corresponding source matches shipped object code.
- [ ] Apache/BSD/MIT notices are retained.
- [ ] INRIA's custom license is reviewed for reused source or assets.
- [ ] Model weights and datasets have separate provenance.
- [ ] README does not describe LichtFeld as BSD-3-Clause.
