# DroneGS native trainer

This directory contains the original C++23/CUDA implementation of the
DroneAI Gaussian trainer. No LichtFeld implementation source is copied here.

Version 0.4 is a deliberately narrow vertical slice. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, images, and sparse points;
- initializes one Gaussian per sparse point;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- provides a CUDA L1 gradient primitive and finite-difference test.

It does **not** yet rasterize images or optimize Gaussian parameters. The
binary identifies itself as `dronegs-fixed-topology` and remains opt-in.

## Container build

From the repository root:

```bash
docker build -t dronegs-dev:0.4.0 -f app1-colmap/dronegs/Dockerfile .

docker run --rm --gpus all \
  --mount type=bind,src="$PWD",dst=/workspace \
  -w /workspace/app1-colmap/dronegs \
  dronegs-dev:0.4.0 \
  bash -lc 'cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release &&
            cmake --build build && ctest --test-dir build --output-on-failure'
```

## Fixed-topology smoke run

```bash
./build/dronegs \
  --data-path /data/colmap-dense \
  --output-path /tmp/dronegs-run \
  --iter 1 --strategy mrnf --sh-degree 0 --max-cap 500000 \
  --resize-factor 4 --max-width 1600 --tile-mode 4 \
  --seed 42 --run-manifest /tmp/dronegs-run/trainer_run.json
```

The output directory must be empty and must not contain the source dataset.
