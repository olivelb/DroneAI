# DroneGS changelog

This changelog covers the standalone Gaussian trainer project.

## 0.5.0-dev.1 - Phase 4 experimental

- Preserved COLMAP world-to-camera poses and added native JPEG decoding.
- Added PINHOLE and SIMPLE_PINHOLE projection with resized intrinsics.
- Added an original CUDA additive Gaussian rasterizer and analytical backward pass.
- Added Adam optimization for DC color and opacity with deterministic camera order.
- Added initial/final anchor loss and training time to run-manifest v1.
- Added an end-to-end GPU convergence test and a GAJAN 25-image smoke report.
- Kept positions, scales, rotations, topology, and non-DC SH coefficients fixed.
- Did not tag `dronegs-v0.5.0`: ordered alpha compositing and the fixed-topology
  quality parity exit gate are not complete.

## 0.4.0 - Phase 3

- Added the original MIT C++23/CUDA DroneGS project and development image.
- Added strict parsing of trainer CLI contract v1.
- Added bounded COLMAP binary camera, image, and sparse-point loading.
- Added fixed-topology Gaussian initialization and atomic binary PLY export.
- Added atomic run-manifest-v1 output with Git provenance.
- Added native COLMAP/CLI/PLY tests and a finite-difference CUDA gradient test.
- Verified the native PLY with DroneAI's existing CuPy `GaussianModel` on GPU.
- Added a containerized LichtFeld baseline suite and Docker-aware VRAM sampling.

## 0.3.0 - Phase 2

- Added a validated backend-neutral training request and normalized result.
- Added LichtFeld and contract-v1 DroneGS subprocess adapters.
- Kept LichtFeld as the default while adding explicit environment and mission selection.
- Wired the existing partitioned orthophoto workflow through the backend boundary.
- Documented the pinned LichtFeld CLI's lack of user-controlled seed support.

## 0.2.0 - Phase 1

- Added a versioned, backend-neutral benchmark suite format.
- Added isolated repeated runs with immutable output directories.
- Added dataset inventory fingerprints and PLY artifact validation.
- Added wall-time summaries and best-effort per-process VRAM sampling.
- Added the five-run GAJAN LichtFeld reference suite.

## 0.1.0 - Phase 0

- Defined the product boundary between DroneAI and its Gaussian trainer.
- Versioned the initial CLI and run-manifest contracts.
- Added the implementation roadmap and phase gates.
- Added the GPL and third-party provenance register.

The production backend remains LichtFeld until all parity gates pass.
