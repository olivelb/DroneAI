# SAM3 without runtime Hugging Face access

The optional `SAM3_MODEL_DIRECTORY` points to a complete local Transformers
snapshot containing `model.safetensors`, model configuration and processor files.
It requires an absolute path and an independently verified `SAM3_MODEL_SHA256`,
including in development. The weight hash is checked before deserialization;
model and processor load with `local_files_only=True`. Missing files and hash
mismatches fail locally without a download fallback.

Package the licensed snapshot into the detection runtime image during controlled
image preparation, then promote and deploy that image by immutable OCI digest.
Keep the snapshot read-only. Verify its model ID, immutable revision and weight
hash against the acquisition record; the hash is not a substitute for recording
the model/configuration origin. Image provenance and promotion controls still
apply to the complete image, including configuration and processor files.

Configure the chart consistently with that image:

```yaml
stageJobs:
  sam3:
    modelDirectory: /opt/models/sam3
    artifactSha256: <independently-verified-64-character-weight-sha256>
```

The shared control environment forwards the directory to the standalone control
worker, or to the legacy embedded controller in development. The standalone HTTP
API keeps scheduling configuration out of its environment.
Generated detection Jobs use `SAM3_MODEL_DIRECTORY`, `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`; their Hugging Face token Secret is omitted. Direct worker
execution can set those same environment variables. Empty `modelDirectory`
preserves the existing pinned remote-loading configuration.

Unit tests verify no download call, pre-deserialization hash rejection, missing
weights, both local-only loader options and removal of the runtime HF secret.
Real Helm renders verify both controller modes and reject incomplete
local settings. No licensed model snapshot was supplied for this audit: model
packaging and real inference with network egress disabled remain deployment
qualification requirements. This change does not claim new inference accuracy,
performance or a completed production offline rollout.
