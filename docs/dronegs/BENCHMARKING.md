# DroneGS benchmarking

The harness runs trainer commands in isolated directories. Production and new
qualification runs use the DroneGS binary. Immutable reports from the pinned
LichtFeld development control remain documented for historical comparison,
but the repository no longer builds or launches that trainer.

## GAJAN reference

For the production backend, point the suite command at the portable native
binary:

```bash
export DRONEGS_BIN=/usr/local/bin/dronegs
export GAJAN_DENSE_PATH="$HOME/droneAI-workspaces/gajan-r2s-full/dense"
```

Run five repetitions into a new output root:

```bash
python tools/benchmark_gaussian_trainers.py \
  docs/dronegs/benchmarks/gajan-v1.example.json \
  --output-root "$HOME/droneAI-workspaces/benchmarks/$(date -u +%Y%m%dT%H%M%SZ)"
```

Each run contains logs, trainer artifacts, and `benchmark_run.json`. The suite
directory contains `benchmark_summary.json` with min/median/P95/max wall time
and best-effort per-process peak VRAM.

## Safety and identity

- Source and output directory trees are separate.
- Existing run directories are never overwritten.
- Every repetition records a requested seed derived from the base seed.
- DroneGS forwards and records the requested deterministic seed.
- The dataset fingerprint includes relative paths and sizes plus full contents
  of the COLMAP sparse binary files.
- Image payloads are not fully hashed so inventory remains practical for
  multi-terabyte collections.
- Success without a readable PLY is recorded as failure.

## Suite placeholders

Commands can use `data_path`, `output_path`, `run_manifest`, `repetition`,
`iterations`, `strategy`, `sh_degree`, `max_cap`, `resize_factor`, `max_width`,
`tile_mode`, and `seed`.

Environment variables use `${NAME}` and are required. Missing values fail
before the subprocess starts.
