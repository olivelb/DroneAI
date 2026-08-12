# Aerial GCP HQ representative-block qualification — 2026-08-12

## Verdict

The corrected high-quality resident-capacity path passed its representative
BIGZEN block gate. Training reached the planned pre-filter population, stayed
inside the RTX 3090 memory envelope, passed the held-out canary and retained
enough Gaussians for the requested 2 cm output. A raster-only replay under
`GAUSSIAN_MAP_COVERAGE_V2` then published valid RGB and height GeoTIFFs.

This qualifies one resident core/buffer block and the reusable
training/filtering/raster contract. It does not yet qualify multi-block seam
quality, the complete mission, AbsGrad candidates, survey accuracy or final
visual parity with Metashape.

## Reproducible scope

| Item | Value |
| --- | --- |
| Candidate code | `67a0aae` |
| Native trainer revision | `b8857f89b26788b873465ef5e7151ea363f220f4-dirty` |
| Native trainer SHA-256 | `b15eb9a6842c0316fc1cec7b95b812c1254d06fba7857e17828487529e425630` |
| Native trainer version | `0.5.0-dev.48` |
| Runtime image | `drone-colmap:f16fe91` |
| Host | BIGZEN, Ubuntu WSL2 |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB |
| Dataset fingerprint | `droneai-colmap-dataset-v3:sha256:b4e858da5e40b2250bdf9a18ecfacd011bbc96678816908418045e5d0834c2b0` |
| Block | row 2, column 3 of a 5 x 6 geographic grid |
| Core area | 29,462 m² |
| Cameras | 59 selected; 51 train and 8 held out |
| Sparse seed | 22,547 points |
| Profile | `high-quality-v3`, `reference-absolute` |
| Training | 30,000 iterations, seed 42, SH degree 3 |
| Requested GSD | 0.02 m/pixel |
| Resident hard ceiling | 12 M Gaussians |
| Planned population | 5.7 M retained; 5.8 M before filtering |

The full run label is
`reference-absolute-retention98-finalrestore`. All source models, manifests,
logs, reports and generated products remain under
`/tmp/droneai-hq-v3-block-r2c3-v2` on BIGZEN. No qualification artifact was
deleted.

## Training and density evidence

| Metric | Result |
| --- | ---: |
| Native training wall time | 6,262.86 s |
| End-to-end first attempt | 6,310.10 s |
| Peak observed VRAM | approximately 10.3 GiB |
| Population at topology freeze | 5,800,000 |
| Population after filtering | 5,739,213 (99.0%) |
| Required population at 2 cm | 5,683,256 |
| Achieved mean spacing | 0.071648 m / 3.582 px |
| Minimum compatible GSD | 0.019902 m/pixel |
| Final held-out PSNR | 22.937 dB |
| Final held-out SSIM | 0.5241 |

The first retention-reserve attempt exposed a final-window defect: pruning at
iteration 14,800 could freeze below the target. The corrected trainer reserves
the minimum final split budget and restored exactly 5.8 M Gaussians before
freezing topology. The retained `point_cloud.ply` has SHA-256
`bec774ae14440fc2bd9c2c295445cdee138b1dae354660626dbd8a2825260529`.

## Boundary-aware coverage replay

The first raster attempt passed global coverage but failed because the V1
strict minimum included two completely empty right-hand corner cells. All five
cells below 1% were on the boundary of the irregular mapped footprint; the
worst interior cell was 23.0%.

`GAUSSIAN_MAP_COVERAGE_V2` keeps boundary cells in the aggregate and
camera-area checks, but applies the strict localized-hole minimum only to cells
surrounded by the expected footprint. The original canary-approved model was
reused; no training iteration was repeated. The replay completed in 60.57 s.

| Coverage metric | Result | Threshold |
| --- | ---: | ---: |
| Valid pixels over expected footprint | 87.0000% | 50% |
| Cells reaching 25% validity | 93.3594% | 75% |
| Worst interior cell | 23.0286% | 1% |
| Camera-cell p10 | 80.0283% | 10% |
| Policy result | accepted | accepted |

The actual core window is 8,502 x 8,970 pixels. It contains 99.9864% valid
pixels, all 256 diagnostic cells exceed 25%, and its worst cell is 99.6505%.
This confirms that missing data belongs to the buffer boundary and not the
uniquely published resident core.

## Products

Both products are 10,978 x 13,331 pixels at exactly 0.02 m/pixel in
`EPSG:32636`, with bounds
`414485.0903, 6634570.0111, 414704.6503, 6634836.6311`. The height product uses
float32 `NaN` NoData.

| Product | Bytes | SHA-256 |
| --- | ---: | --- |
| RGB GeoTIFF | 438,496,797 | `83d1904540221104c6b703afa9e6c34dfe948b287451f214f964a09a1039815e` |
| Height GeoTIFF | 436,860,751 | `d62accfb2372f5bf1d9c4c24d4b029b86dd69ae17fbad6c1f6e675dbe004e66e` |
| Coverage report V2 | 31,018 | `b5490269b8b96582f95e8efaa8ae341cbb52635c07e94cbdbc45f7bd6aa72c8a` |
| Completed run report | 43,579 | `af44d61b8f9a8e323123e8e5c482020a0341090725130be679d0663d58c9c4b1` |

Compact full-buffer and core previews are retained in the workspace under
`qualification_previews/`. Their SHA-256 values are respectively
`e90150a99832735d16c38ecb002a681cf4df0e2bf6d7bebc7deb7ee66c45ae26`
and `d385d9534d4b7b99ad9d0b60204460da2d542aa38e46cc1dd6cf028b3b599012`.

No seam report is expected for this single resident block. A multi-block
representative mission remains the required evidence for the core/buffer seam
metrics before production enablement.

## Validation

- 28 focused Python tests passed, including seven spatial-coverage tests;
- full static validation passed: compileall, Ruff ratchets, strict mypy,
  shellcheck, Markdown links, qualification evidence, schemas and platform
  version;
- the raster-only BIGZEN replay exited 0 and produced both GeoTIFFs;
- visual inspection of the core preview found continuous coverage without the
  boundary artifacts visible in the surrounding buffer.
