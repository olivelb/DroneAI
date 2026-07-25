# DroneGS native trainer

This directory contains the original C++23/CUDA implementation of the
DroneAI Gaussian trainer. No LichtFeld implementation source is copied here.

Version `0.5.0-dev.8` is an experimental fixed-topology training slice. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, poses, images, and sparse points;
- decodes JPEG training images and scales pinhole intrinsics;
- stores decoded RGB as bytes in a lazy 256 MiB LRU cache;
- overlaps one JPEG decode with GPU work through a persistent prefetch worker;
- defines a tested CPU oracle for depth-sorted front-to-back alpha composition;
- provides a forward CUDA renderer with GPU projection, stable radix sorting,
  GPU-built 16x16 tile ranges, and shared splat batches that match the CPU
  alpha oracle;
- provides tested CPU and CUDA ordered-alpha gradients for DC color and opacity,
  including equal-depth ordering, alpha clamping, and early-transmittance exit;
- trains through a persistent ordered-alpha CUDA context with reusable
  projection, CUB, tile, gradient, and Adam buffers;
- computes active-pixel RGB8 L1 gradients, ordered-alpha backward, and Adam
  updates on device without copying Gaussian gradients through the host;
- initializes one Gaussian per sparse point;
- projects fixed Gaussians and rasterizes additive screen-space kernels on CUDA;
- back-propagates active-pixel L1 gradients;
- updates DC color and opacity with Adam in deterministic camera order;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- provides finite-difference and end-to-end convergence tests.

It is not a LichtFeld replacement yet. The experimental training path now uses
front-to-back ordered-alpha composition, while the additive path remains only
as a convergence control. Positions, scales, rotations, topology, and non-DC
SH coefficients remain fixed. Only
`SIMPLE_PINHOLE` and `PINHOLE` cameras are accepted and quality parity is not
measured. Decoded images use a bounded LRU plus one in-flight prefetch slot.
Pinned transfer buffers and asynchronous host-to-device copies are not retained:
the current Albagnac prototype measured only about 0.06 seconds of upload service
over 500 iterations. The binary identifies itself as
`dronegs-fixed-topology-ordered-alpha-prototype` and remains opt-in.

## Container build

From the repository root:

```bash
docker build -t dronegs-dev:0.5.0-dev -f app1-colmap/dronegs/Dockerfile .

docker run --rm --gpus all \
  --mount type=bind,src="$PWD",dst=/workspace \
  -w /workspace/app1-colmap/dronegs \
  dronegs-dev:0.5.0-dev \
  bash -lc 'cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release &&
            cmake --build build && ctest --test-dir build --output-on-failure'
```

The optional raster benchmark is built with
`-DDRONEGS_BUILD_BENCHMARKS=ON` and run as:

```bash
./build/dronegs_rasterization_cuda_benchmark 1025093 5
./build/dronegs_rasterization_cuda_benchmark 1025093 5 backward
```

The default mode measures the complete ordered-alpha forward call. The
`backward` mode measures forward plus DC/opacity backward. Both include
allocation, projection, sorting, tile construction, rendering, and host readback.

## Experimental fixed-topology training run

```bash
./build/dronegs \
  --data-path /data/colmap-dense \
  --output-path /tmp/dronegs-run \
  --iter 1 --strategy mrnf --sh-degree 0 --max-cap 500000 \
  --resize-factor 4 --max-width 1600 --tile-mode 4 \
  --seed 42 --run-manifest /tmp/dronegs-run/trainer_run.json
```

The output directory must be empty and must not contain the source dataset.
