# DroneGS native trainer

This directory contains the original C++23/CUDA implementation of the
DroneAI Gaussian trainer. No LichtFeld implementation source is copied here.

Version `0.5.0-dev.1` is an experimental fixed-topology training slice. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, poses, images, and sparse points;
- decodes JPEG training images and scales pinhole intrinsics;
- initializes one Gaussian per sparse point;
- projects fixed Gaussians and rasterizes additive screen-space kernels on CUDA;
- back-propagates active-pixel L1 gradients;
- updates DC color and opacity with Adam in deterministic camera order;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- provides finite-difference and end-to-end convergence tests.

It is not a LichtFeld replacement yet. Rendering is additive rather than
front-to-back alpha composited; positions, scales, rotations, topology, and
non-DC SH coefficients remain fixed. Only `SIMPLE_PINHOLE` and `PINHOLE`
cameras are accepted, quality parity is not measured, and all decoded images
are cached in host RAM. The binary identifies itself as
`dronegs-fixed-topology-additive-prototype` and remains opt-in.

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
