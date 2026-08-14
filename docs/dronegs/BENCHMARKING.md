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
  --output-root "$HOME/droneAI-workspaces/benchmarks/$(date -u +%Y%m%dT%H%M%SZ)" \
  --bundle
```

Each run contains logs, trainer artifacts, and `benchmark_run.json`. The suite
directory contains `benchmark_summary.json` with min/mean/median/P95/max wall
time, standard deviation, 95% mean interval and best-effort per-process peak
VRAM. Hardware observations retain GPU, driver, CUDA, temperature and
available power-limit fields independently; unsupported `[N/A]` values do not
discard the remaining inventory. `--bundle` writes a portable `.tar.gz`
containing reports, logs and artifacts.

Native dev.53 logs also contain six `gpu_stage_telemetry` JSON events. They
separate projection, projected-record sort, binning/duplication, tile/depth
pair sort, FastGS bucket construction, rasterization, objective, backward and
optimizer GPU time. `preprocess_ms` remains the sum of the first five fields
for report compatibility. Samples are staggered one step after the
optimizer-statistics cadence so the Adam measurement excludes diagnostic
atomics; they are profiling evidence, not an additional quality metric or a
production gate.

## Safety and identity

- Source and output directory trees are separate.
- Existing run directories are never overwritten.
- Harness logs live beside the trainer output, which must be a new,
  artifact-free directory.
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

## SAVERES production V1

The committed `docs/dronegs/benchmarks/saleres-production-v1.json` suite runs
five 15,000-step seeds against `${SALERES_DENSE_DATASET}`. The 2026-07-28
qualification completed 5/5 runs; its lightweight aggregate, binary/dataset
identity and five PLY hashes are recorded in
`docs/benchmarks/saleres-dronegs-production-v1-2026-07-28.json`.

## Helenenschacht ultra-resolution diagnostic

The
[Helenenschacht 5 mm report](../benchmarks/helenenschacht-dronegs-ultra-5mm-2026-07-30.md)
records a single deliberately demanding custom run: factor 1, width 4096,
30,000 steps, 2 million Gaussians and a 5 mm/pixel COG. It took 6404 seconds,
passed PSNR, failed the immutable production SSIM gate and retained 5,0 cm
horizontal GCP RMSE from the reused sparse alignment.

This is negative qualification evidence, not a production profile. It shows
that output resolution, held-out image quality and survey accuracy are
separate gates. Any diagnostic threshold override must remain visible in the
run manifest, and any modified V1 trainer or canary parameter must be recorded
as `custom`.
