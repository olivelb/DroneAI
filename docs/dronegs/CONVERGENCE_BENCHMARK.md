# DroneGS convergence measurements and checkpoint step benchmark

These opt-in diagnostics support decisions about iteration budgets and CUDA changes. They do not enable early stopping, change the production profile, reduce overlap, or replace any optimizer kernel.

## Periodic held-out evaluation

Add `--eval-every 5000 --eval-start 10000` to an existing native command with a held-out split, for example `--test-every 8`. Keep the original `--iter 30000` and all training settings. This measures points along the same 30k trajectory; separately training with `--iter 15000` changes growth, learning rates and the photometric finish, and answers a different question.

- `--eval-every N`: evaluate at absolute iteration multiples of N; zero (default) disables intermediate evaluations.
- `--eval-start N`: ignore scheduled points below N; zero (default) starts at the first interval. Requires a nonzero interval.
- Initial and final evaluation retain their existing behavior. The last iteration uses the final evaluation only. A requested `--stop-after` still evaluates if that iteration is a scheduled point, then returns the existing pause status 75.
- Intermediate evaluations never save prediction images. `--save-eval-images` retains its existing initial/imported-model and final behavior.
- Resumes use absolute iteration numbers and skip already completed iterations. These observational settings are deliberately outside the checkpoint configuration fingerprint, so they can be enabled or disabled on resume.

Outputs:

| Output | Contents |
| --- | --- |
| `evaluation/metrics.csv` | Existing per-view metrics, with additional `iteration_N` stages |
| `evaluation/curve.csv` | Aggregate PSNR/SSIM, pixel-weighted metrics, Gaussian count and times at each point |
| stdout `evaluation_summary` | Same aggregate measurements for live monitoring |
| run manifest | Evaluation options, aggregate evaluation duration and `periodic_evaluation_seconds`; curve artifact indexed |

`training_seconds` excludes image-cache waits and time spent in intermediate evaluations. Cache waits during evaluation are subtracted only once. It still includes topology work and checkpoint blocking, as before; it is not pure CUDA time. Evaluation duration includes held-out image waits. Decoding/prefetch may overlap other work, so these timings are not a disjoint accounting of every wall-clock phase.

On resume, times are relative to the current process. `session_start_iteration` identifies its starting point; do not connect separate sessions into a cumulative curve without their run metadata. Use a separate output directory for each experimental invocation. Existing CSVs are appended on resume, including a header when the destination file is new.

Evaluation uses the same held-out views, tiling, black background and PSNR/SSIM definitions as the existing final evaluation. It does **not** add a spatial core mask or measure mesh/orthophoto accuracy. Held-out views used to select an iteration budget become validation data; keep another area or independent split for final quality qualification.

## Reproducible CUDA step measurements

Build with `-DDRONEGS_BUILD_BENCHMARKS=ON`. The separate executable is `dronegs_training_step_benchmark`; the normal executable does not accept benchmark-only flags. Build candidate and baseline with the same compiler, flags and GPU architecture for performance comparisons.

The native benchmark accepts the original training command's options, a required `--resume-from`, an empty output directory, and:

| Flag | Default | Range |
| --- | --- | --- |
| `--benchmark-views` | 3 | 1–100, no more than available training frames |
| `--benchmark-repeats` | 5 | 1–100 per view |
| `--benchmark-warmups` | 1 | 0–100 discarded repetitions per view |

Checkpoint writes, pause requests and periodic evaluation are rejected by the benchmark executable. It exports neither a PLY nor a completed training manifest. It writes `step_benchmark.csv` and JSON log events.

Protocol:

1. Reconstruct the original frame descriptors and training split using the original sparse initialization. Validate the checkpoint's dataset and training configuration fingerprints.
2. Select a deterministic shuffled subset of training frames with the original seed. Copy their decoded RGB data into owned host buffers, so cache eviction cannot invalidate benchmark inputs.
3. Before **every** warmup and measured step, restore the full checkpoint, including Gaussians, Adam moments, step, active SH and refinement statistics. No topology refinement is performed.
4. Render the selected view without updates for at least 250 ms after restoration to put load on the GPU. Record conditioning duration and render count separately. This reduces idle downclock effects; it does not lock GPU clocks or guarantee identical thermal conditions.
5. Measure one synchronized training step with forced CUDA stage telemetry. Record fenced host time, stage timings, view identity, image dimensions, Gaussian count, loss and actual step.

For a final 30k checkpoint this probes **step 30001** from the saved state, with the final photometric objective. It is a diagnostic of a mature model, not an extension of the training run or an estimate of early densification cost. An intermediate checkpoint probes its actual next step and its corresponding statistics/objective policy. Use checkpoints from different training phases before generalizing a kernel result.

Restoration, checksum validation, image decoding and conditioning are excluded from the measured step. Host-to-device target transfer and metric readback remain part of the native step. CUDA events add instrumentation cost. The repeated view is warm; do not interpret this as complete production throughput or compare it directly with uninstrumented steps. Review per-view dispersion and GPU conditions before reporting a speedup.

### Preserving a reproducible experiment

The Python runner accepts an original command JSON (`{"argv": [...]}` or an argv list), rewrites only execution paths and observational options, hashes the executable and checkpoint, captures GPU information and validates the sample identities. It never launches a shell to interpret the saved command.

```bash
python3 app1-colmap/dronegs/tools/benchmark_training_steps.py \
  --command-json /results/commands/o20-r2c2.json \
  --binary /build/dronegs_training_step_benchmark \
  --data-path /scratch/datasets-fast/attempt-2/o20-r2c2 \
  --checkpoint /checkpoints/o20-r2c2.ckpt \
  --output /results/new-step-benchmark \
  --source-revision COMMIT-PLUS-PATCH-OR-SOURCE-TREE-HASH \
  --views 3 --repeats 5 --warmups 1
```

The output directory must be new. `benchmark.json` contains status, exact argv, input/output checkpoint hashes and per-view median, minimum, maximum and median absolute deviation. Keep it with `native.log`, `native/step_benchmark.csv`, the matching source snapshot and build logs. A failed run retains its artifacts. Interrupted runs must not be treated as completed measurements.

Large checkpoints may be copied to local scratch storage before a benchmark. Verify the copy's hash and retain the source. This improves experiment turnaround; it is not evidence for a production/cloud I/O speedup. Dataset image links must resolve in the execution environment; Saint-Étienne's existing links use the `/input` container mount.

## Validation

The native tests compare serialized checkpoints before/after a forward evaluation and compare complete training checkpoints with periodic evaluations, densification and pause/resume. The branches start from the same seed checkpoint: independently recomputing the initial loss can differ by one ULP because of existing GPU reductions. CLI tests cover disabled defaults and invalid intervals. Python tests reject changed identities, missing/duplicate samples and non-finite timings, and verify that command rewriting removes checkpoint writes.

## Read-only checkpoint evaluation

`dronegs_checkpoint_evaluation_probe` evaluates a saved native checkpoint with the existing held-out metric definitions. It exits before creating a training schedule or calling a training step; it writes no checkpoint, PLY or completed training manifest.

Pass diagnostic name/value options before `--`, followed by the original native training options. Keep the original initialization, dataset identity, tiling, seed, split and `--iter` budget. Set `--resume-from` to the checkpoint and choose a new output directory outside the dataset tree. Omit checkpoint writes and pause requests, and set periodic evaluation and `--save-eval-images` to zero.

| Diagnostic option | Contract |
| --- | --- |
| `--expected-completed-iteration N` | Required; native restored progress must equal N |
| `--binary-sha256 SHA` | Required lowercase SHA256 supplied by the launcher; native code records it but does not verify it |
| `--expected-held-out-frames N` | Optional count assertion; zero disables it |
| `--repeats N` | 1–10, default 1; repeats use the same loaded context |
| `--export-frames none/all/ID,...` | Optional prediction export; default none; IDs are frame indices, and every requested frame must be held out |

The probe reconstructs frame support from the original initialized model **before** restoring the checkpoint. Rebuilding that support from the trained model would change the evaluation population. The native loader validates checkpoint checksum, runtime and dataset/configuration identities; the probe also checks iteration and selected frames before creating output. No arbitrary checkpoint fingerprint override is accepted.

Each `repeat-N` contains `evaluation/metrics.csv` and `views.jsonl`, which records frame indices, crop coordinates, image dimensions, cameras and export identities. Selected frames additionally contain `.rgb.f32` predictions (little-endian float32, HWC RGB), `.prediction.ppm` and `.target.ppm`. Float RGB preserves values without display quantization; exported values must be finite. `diagnostic-complete.json` is written only after all requested repeats and exports finish.

Same-context repeats are a reproducibility check, not independent checkpoint reloads or a guarantee of bitwise equality on every GPU. The caller must independently hash the executable and input checkpoint, preserve build provenance, and compare common view/crop identities before comparing outputs from different runs. Photometric metrics and rendered images do not establish geometric accuracy.

The CPU request tests cover malformed arguments, incompatible side modes and output containment. The GPU fixture checks native metric parity with a paused training evaluation, unchanged source bytes, repeated float RGB exports, no training artifacts, and rejection of corrupt or mismatched checkpoints before output creation. These tests must run against the delivered source tree; historical experimental build results do not qualify a newly extracted build.
