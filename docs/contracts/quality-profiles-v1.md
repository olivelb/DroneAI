# Quality profiles v1

DroneAI exposes three immutable end-to-end envelopes through
`GET /mission/parameters`. The profile ID, version, effective COLMAP/DroneGS
parameters, explicit envelope overrides and selected YOLO artifact identity are
persisted with every new mission and included in its Kafka event.

| Profile ID | Image width | SIFT features | DroneGS iterations | Gaussian cap | Training downscale |
|---|---:|---:|---:|---:|---:|
| `fast-v1` | 1,600 px | 2,048 | 7,500 | 1,500,000 | 8 |
| `normal-v1` | 2,400 px | 4,096 | 15,000 | 3,000,000 | 4 |
| `high-quality-v1` | 4,096 px | 16,384 | 30,000 | 5,000,000 | 1 |

`normal-v1` is the API and dashboard default. Selecting a profile applies its
values to feature extraction, MVS image preparation and DroneGS. Expert values
remain allowed and are recorded under `quality_profile_overrides`; if they
change the DroneGS training identity, the worker records the executed DroneGS
recipe as `custom` while retaining the requested end-to-end profile and its
override provenance.

The same parameter response publishes the deployment-approved YOLO model
catalog. Each entry includes repository, revision, artifact name, approved
SHA-256, availability and the complete native OBB class list. The optional
`AERIAL_AVAILABLE_MODEL_VARIANTS` comma-separated environment variable limits
what a deployment advertises and accepts. An empty value exposes all approved
registry entries. The dashboard never maintains a separate class or model
constant.
