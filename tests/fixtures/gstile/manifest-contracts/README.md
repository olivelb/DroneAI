# Shared manifest contract corpus

Python, TypeScript and Windows C++ execute every JSON case. Filenames ending in
`-valid.json` must pass; `-invalid.json` must fail. Unary/ternary cases otherwise
have consistent populations and contiguous ranges, so they isolate the binary
hierarchy contract. Maximum/oversized packs are metadata only, without allocating
large payloads. Digests and CRC values are synthetic: this corpus tests manifest
admission, not payload integrity (covered separately by native and codec tests).

Pack limit: 128 MiB including the Q96 header, also for stream-only virtual packs.
It retains the producer's existing maximum leaf of 1,048,576 records (96 MiB plus
header), while bounding base + SH + Q96 reconstruction to about 256 MiB before
other CPU/GPU allocations. Production target remains 2 MiB. Existing oversized
bundles must be regenerated. Manifest files are limited to 8 MiB by producer,
HTTP reader and native viewer. Native loading still verifies complete streams;
range loading needs an independently authenticated range format and is not
claimed by this resource-bound correction.
