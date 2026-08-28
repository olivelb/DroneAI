# GSTile v1 — baseline interchange contract

Status: **implementation baseline**. The normative implementation lives in
`app1-colmap/gaussian_tiles` and the browser decoder must reject incompatible
major versions.

## 1. Scope and invariants

GSTile is the immutable, range-addressable representation used to stream
DroneGS results. Version 1 deliberately preserves the DroneGS rendering model:

- position, logarithmic scale and quaternion rotation;
- base opacity as a **logit**;
- colour spherical harmonics through degree 3 (3 DC + 45 residual values);
- the 15 non-constant directional-opacity SH residuals;
- a stable unsigned 64-bit source identifier.

Directional opacity is evaluated exactly as:

```text
alpha(direction) = sigmoid(
  opacity_logit + sum(opacity_sh[i] * sh_basis[i + 1](direction))
)
```

Dropping `opacity_sh`, applying it after the sigmoid, or treating opacity as a
linear value is a contract violation.

The baseline profile is loss-bounded and uses 96 bytes per Gaussian. A later
compact profile may use codebooks, but it must have its own profile identifier
and pass the same parity gates. The 20–32 B/Gaussian target is therefore a
benchmark goal, not part of the v1 baseline contract.

## 2. Bundle layout

```text
bundle/
  manifest.json
  packs/
    <node-id>.gst
    <node-id>.gst.zst
```

All paths in the manifest are relative, use `/`, and must not contain `..`.
Bundles and packs are immutable. A new build produces a new bundle identifier;
it never mutates a published pack.

## 3. Manifest

Required top-level fields:

```json
{
  "schema": "droneai-gstile",
  "version": 1,
  "profile": "dronegs-sh3-opacity-sh3-q96",
  "bundleId": "sha256:<hex>",
  "source": {
    "sha256": "<hex>",
    "gaussianCount": 1,
    "colorShDegree": 3,
    "opacityShDegree": 3
  },
  "coordinateFrame": {
    "kind": "local",
    "origin": [0.0, 0.0, 0.0],
    "crs": null
  },
  "root": "r",
  "nodes": [],
  "packs": []
}
```

Each node has `id`, `bounds.min`, `bounds.max`, `gaussianCount`, and either
`children` or a `tile`, never both. Bounds enclose Gaussian centres. A tile has
`pack`, `byteOffset`, `byteLength`, `recordCount`, `sha256` and `quantization`.
The [production policy](gstile-production-defaults-v1.md) defaults to a 2 MiB
`pack_target_bytes` target, grouping exact tiles and proxies into separate,
bounded packs. Explicit `None` (CLI `--individual-packs`) retains individual
representation packs. Aggregated
representations use the `depth-spatial-v1` layout: a pack contains one
representation kind at one tree depth, ordered by spatial node identifier.
A LOD cut normally selects a parent or its descendants, not both; mixing
depths therefore creates systematic byte overfetch even when it reduces the
request count.
Every tile keeps its own quantization and occupies an aligned, non-overlapping
payload range. Tile ranges must cover the complete pack payload in ascending
offset order. Their shared SHA-256 identifies the canonical aggregate pack.
This extension is additive within version 1 because readers were already
required to use the declared byte range rather than assume offset 32 or a
one-to-one tile/pack relationship.

Aggregating writers report `statistics.packCount`,
`statistics.representationCount`, `statistics.packTargetBytes`, and optionally
`statistics.packGrouping` so request reduction and overfetch experiments remain
reproducible from the manifest.

Existing immutable bundles can be migrated without decoding or requantizing
their Q96 records:

```bash
python tools/repack_gstiles.py SOURCE_BUNDLE OUTPUT_BUNDLE \
  --pack-target-bytes 2097152 --progress-jsonl
```

The repacker verifies every source pack SHA-256, header and payload CRC before
copying its declared tile ranges. Publication is atomic and the output receives
a new content-derived bundle identifier.

A pack may expose an additive `encodings.zstd` object containing `path`,
`byteLength` and `sha256`. It is a lossless transport representation of the
complete canonical `.gst` object, uses a zstd frame with content size and
checksum, and is emitted only when smaller than the canonical object. Readers
that do not support zstd ignore this field and fetch `path` unchanged.

`quantization` contains the min/max or symmetric scale needed to decode every
field. Arrays have fixed lengths: 3 for position/scale/DC, 45 for colour SH
residuals and 15 for opacity SH residuals.

Unknown additive manifest fields must be ignored. Unknown schema, major
version, profile, record size, required flag, or malformed bounds must fail
closed before GPU allocation.

## 4. Pack header

All integers are little-endian. The fixed 32-byte header is:

| Offset | Type | Meaning |
|---:|---|---|
| 0 | `char[8]` | `GSTILE1\0` |
| 8 | `uint16` | major version (`1`) |
| 10 | `uint16` | header bytes (`32`) |
| 12 | `uint16` | record bytes (`96`) |
| 14 | `uint16` | required flags (`0`) |
| 16 | `uint32` | record count |
| 20 | `uint64` | deterministic node hash |
| 28 | `uint32` | CRC32 of the record payload |

The manifest SHA-256 covers the complete pack. The CRC is a fast corruption
check after a range fetch; SHA-256 remains the publication integrity check.

## 5. Baseline 96-byte record

| Offset | Encoding | Field |
|---:|---|---|
| 0 | `uint16[3]` | position |
| 6 | `uint16[3]` | logarithmic scale |
| 12 | `int16[4]` | normalized quaternion |
| 20 | `uint16` | opacity logit |
| 22 | `int16[3]` | colour DC |
| 28 | `int8[45]` | colour SH residuals |
| 73 | `int8[15]` | directional-opacity SH residuals |
| 88 | `uint64` | stable source id |

Bytes 88–95 are naturally occupied by the identifier; no implicit padding is
part of the record. Unsigned values use affine min/max quantization. Signed
values use symmetric per-component scales. Quaternions are renormalized after
decoding and invalid zero/non-finite quaternions are rejected by the writer.

## 6. HTTP and cache contract

The browser obtains the manifest through an authenticated API, then fetches
pack ranges using `Range: bytes=start-end`. Servers must return either `206`
with a valid `Content-Range`, or `200` only when the requested range is the full
object. Cache keys include bundle id, pack path, byte range and SHA-256.

When `encodings.zstd` is selected, the browser fetches the complete encoded
object, decodes it before Q96 parsing, verifies the canonical pack SHA-256, and
caches only the canonical decoded bytes. Thus encoded and unencoded clients
share the same immutable cache identity and rendering input.

When several requested tiles share a pack, the scheduler coalesces their fetch
under that same immutable identity. Aggregation is intentionally bounded: a
writer may exceed the configured target only when one tile is larger than the
target, and exact tiles are never mixed with LOD proxies so prefetch does not
force unrelated representation classes into memory.

Authenticated descriptors advertise the zstd URL only when the request's
`Accept-Encoding` includes `zstd`. The signed object response declares
`Content-Encoding: zstd`, so decompression runs in the browser networking
stack. Encoded objects use a complete GET without `Range`, because Chromium
rejects compressed `206 Partial Content` responses even when the requested
range covers the complete representation. Static-bundle readers may instead
use `DecompressionStream("zstd")`
when available. Both paths fall back to the canonical `.gst` URL.

Parents remain resident until all selected children are decoded and uploaded.
Eviction is resource-driven, not React-state-driven. A failed child fetch must
leave the last complete representation visible.

## 7. Required validation gates

1. Deterministic output hashes for identical input and options.
2. Bounded-memory tiling and preflighted temporary/output disk space.
3. Decode error bounds reported for every quantized field.
4. Directional opacity parity against DroneGS over representative directions.
5. Colour/geometry parity against the source PLY.
6. Corruption, traversal, oversize-count and incompatible-version rejection.
7. Browser range, cancellation, cache and device-loss tests.

Hierarchical replacement LOD, codebook compression, geospatial collision data
and edit overlays are compatible extensions, but are not silently inferred by
baseline readers.
