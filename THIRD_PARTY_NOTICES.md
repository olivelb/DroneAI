# Third-party notices

The MIT license in `LICENSE` applies only to the original DroneAI source code.
The project integrates, patches, downloads, or runs third-party software and
model weights under their own licenses.

The most important license-sensitive components are:

- COLMAP and PyCOLMAP — BSD 3-Clause.
- LichtFeld-Studio — see the license in the pinned upstream repository.
- Ultralytics YOLO — AGPL-3.0 or an applicable commercial license.
- Meta SAM 3 source and gated model weights — Meta's applicable SAM license
  and the terms published with the model.
- NVIDIA CUDA base images and libraries — NVIDIA container and CUDA terms.

The dependency and citation table in `README.md` contains the broader
repository-specific inventory. Built container images should be audited
separately because they include additional operating-system and Python
packages.
