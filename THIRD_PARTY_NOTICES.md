# Third-party notices

The MIT license in `LICENSE` applies only to the original DroneAI source code.
The project integrates, patches, downloads, or runs third-party software and
model weights under their own licenses.

The most important license-sensitive components are:

- COLMAP and PyCOLMAP — BSD 3-Clause.
- DroneGS — original orchestration is covered by the repository license; the
  combined MRNF/FastGS CUDA translation units and linked native binary are
  GPL-3.0-or-later. Matching source is shipped in the runtime image.
- LichtFeld-Studio — GPL-3.0-or-later; historical source/provenance reference
  for the GPL-covered DroneGS adaptations listed in the provenance register.
- Ultralytics YOLO — AGPL-3.0 or an applicable commercial license.
- Meta SAM 3 source and gated model weights — Meta's applicable SAM license
  and the terms published with the model.
- NVIDIA CUDA base images and libraries — NVIDIA container and CUDA terms.

The dependency and citation table in `README.md` contains the broader
repository-specific inventory. Built container images should be audited
separately because they include additional operating-system and Python
packages.

## Native Windows GSTile viewer

The native viewer uses nlohmann/json 3.12.0 (MIT); its license is included in the portable package. Windows SDK and Direct3D remain subject to Microsoft terms. No DroneGS CUDA or PlayCanvas runtime is linked into this standalone executable.
