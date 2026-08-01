# Counter-analysis of the 2026-08-01 repository audit

Audited baseline: `main` at merge `8f6b7ac73bf74656de8f3920396006160cf8a119`.

This note records which findings were reproducible, what was changed, and what
still requires research or broader field validation. The GitHub CI run for the
audited merge does exist and completed successfully:
<https://github.com/olivelb/DroneAI/actions/runs/30694602404>.

## Findings validated and corrected

| Audit finding | Counter-analysis and implementation |
| --- | --- |
| Unknown non-zero RTK flags were accepted | Valid. RTK states are now classified conservatively as `fixed`, `float`, `single`, `invalid`, or `unknown`. Only known fixed solutions enter pose-prior refinement. Current Autel `50` records remain accepted. |
| MRK association relied mainly on sequence number | Valid. Ambiguity checks remain, and fixed-RTK XMP position plus binary EXIF capture time now provide independent geographic and temporal checks. A six-sigma bounded position mismatch or a timestamp delta above 5 s rejects the association. Helenenschacht validates all 176 positions, with maximum deltas below 0.7 mm horizontal and 5 mm vertical. |
| GCP Cauchy loss was component-wise | Valid. Robustification now applies once to each point's 3-D Mahalanobis norm, while retaining three residual components to constrain the seven-parameter Sim(3). |
| Zero checkpoints could be confused with verified accuracy | Valid. Every GCP report now records `accepted-verified`, `accepted-unverified`, or `rejected`. Adjustment residuals are explicitly separated from independent checkpoint metrics. Checkpoints can be mandatory by mission policy. |
| No GCP promotion gate or spatial check | Valid. Promotion now checks surveyed adjustment baseline, checkpoint count, horizontal RMSE, vertical RMSE, and maximum normalized error. Failed transforms are reported but not written to the active georeferencing outputs. |
| RTK output was promoted merely because a model existed | Valid. The visual and RTK candidate models are both retained and compared. Promotion requires no registered-camera loss, an acceptable point-count ratio, bounded reprojection and median-track degradation, and bounded focal-length drift. Without GCP this is explicitly labelled an internal visual check, not independent accuracy verification. Cached RTK candidates are re-evaluated before reuse. |
| Sim(3) rotation broke directional SH coherence | Valid. Geometry can remain transformed for numeric and checkpoint compatibility, but the geographic view direction is now mapped back with `Rᵀ` before SH evaluation. This is equivalent to preserving the learned SH frame without implementing coefficient rotation. |
| Training identity and qualification thresholds were mixed | Valid. `DRONEGS_PRODUCTION_PROFILE_V1` remains the training recipe; `DRONEGS_QUALIFICATION_POLICY_V1` separately identifies canary thresholds. Changing PSNR/SSIM thresholds records a custom qualification policy without renaming an otherwise identical training recipe. Strict trainer compatibility no longer includes canary thresholds. |
| Required scientific artifacts were optional | Valid. RGB COG, DSM COG, their metadata/previews, filtered final PLY, trainer manifest, canary result, and product manifest are now required verified uploads. GCP missions additionally require the transform, GCP report, and all three `sparse_geo` model files. A failure prevents `DONE`. |
| No final product provenance graph | Valid. `product_manifest.json` hash-links sparse model files, RTK/IMU/GCP/Sim(3) reports, DroneGS training and qualification manifests, final PLY, filter/render parameters, RGB COG, DSM COG, metadata, and previews. Dataset and trainer-binary identities remain transitively linked through the hashed trainer manifest. |

## Findings already correct or intentionally unchanged

- The IMU/gravity prior remains disabled by default. The audit's conclusion is
  correct: it is a recovery aid for weak or oblique networks, not a general
  precision multiplier.
- GCP adjustment remains a global seven-parameter Sim(3), not a survey-control
  bundle adjustment. Documentation and runtime reports must retain that
  distinction.
- A 5 mm output on Helenenschacht remains oversampled relative to the source
  GSD. The 1 cm product remains the defensible production compromise.
- The vertical reference remains explicit and is not silently presented as an
  orthometric national datum.
- The Metashape comparison supports only the stated project/configuration, not
  a general superiority claim.

## Partially addressed or still open

- Camera-pose, intrinsic, distortion, and cross-camera covariance are not yet
  propagated into GCP ray covariance. This requires a defensible approximation
  or a real joint bundle-adjustment covariance, not an arbitrary inflation.
- The GCP gate does not yet compare the accepted transform with a separately
  generated GNSS transform on the same held-out checkpoints. It does prevent a
  bad absolute promotion, but it does not prove improvement over GNSS.
- Timestamp comparison assumes the contemporary 18-second GPS/UTC offset and
  searches legal whole-hour EXIF timezone offsets because these Autel images do
  not encode an explicit offset. Older pre-2017 datasets need a leap-second
  table before this can be considered a universal temporal validator.
- A true GCP bundle adjustment, lever-arm/boresight/time-offset model,
  conventional orthophoto branch, and multi-scene qualification campaign remain
  research and validation work.
- Shared ingress rate limiting, public multi-tenant authentication hardening,
  and a configurable non-root COLMAP container are deployment work and were not
  changed by this geometry-focused patch.
- Real CUDA execution remains conditional on the self-hosted GPU workflow. CPU
  contract tests and portable CUDA compilation do not replace release tests on
  actual Ampere and Ada/Blackwell devices.

## New issue found during counter-analysis

The runtime fallback for `gs_canary_min_ssim` still used the historical value
`0.35` even though the versioned defaults use `0.25`. Normal mission parameter
merging masked the discrepancy, but direct or incomplete callers could receive
a different qualification result. The fallback now comes from the single
profile source of truth.

The production runtime image also pinned CuPy 14 without its `ctk` extra. CuPy
could see the GPU but failed its first JIT kernel with `Failed to find CUDA
headers`. The lock now includes the pinned CUDA 12 toolkit components required
by CuPy. After installing the corrected lock in the runtime image, all 21
orthographic GPU tests pass on the local RTX 4070 Laptop GPU.

## Default promotion thresholds introduced

The defaults are intentionally conservative but configurable:

- RTK sparse point ratio: at least `0.90` of the visual baseline;
- RTK mean reprojection degradation: at most `+0.10 px`;
- RTK median track-length loss: at most `25%`;
- RTK median focal-length change: at most `2%`;
- GCP adjustment baseline: at least `5 m`;
- checkpoint horizontal RMSE: at most `0.10 m`;
- checkpoint vertical RMSE: at most `0.20 m`;
- checkpoint maximum normalized error: at most `5 sigma`.

Checkpoints are not mandatory by default for backwards compatibility, but a
zero-checkpoint product is now unambiguously `accepted-unverified`. Survey
deployments should enable `gcp_require_checkpoints`.
