# GSTile v1 — current Q96/V4 interchange contract

Status: **implementation baseline**. The normative implementation lives in
`app1-colmap/gaussian_tiles` and the browser decoder must reject incompatible
major versions and retired profiles. The sole supported profile is
`dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4`; the binary pack version stays 1.

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
    <pack-id>.gst.base
    <pack-id>.gst.sh
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
  "profile": "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
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
bounded packs. Absent LOD or pack sizes are rejected. Aggregated
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

Retired profiles are not migrated or replayed. Supported Q96/V4 bundles can
be converted losslessly to stream-only storage by tools/split_gstile_attributes.py;
this does not rerun training, quantization or LOD generation.

A historical pack may expose an additive `encodings.zstd` object containing `path`,
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

Hierarchical adaptive replacement LOD is required. Other representation
profiles need a separate contract and qualification; readers do not infer them.

## Independently authenticated attributes (streams v1)

New bundles use storage: "streams": only .gst.base and .gst.sh exist on disk.
The pack path, byteLength, sha256, byteOffset and tile offsets describe the
**virtual Q96 layout**, not a physical .gst file. q96Header stores its 32-byte
header as 64 lowercase hexadecimal characters. Together with both streams it
reconstructs byte-exact Q96 for validation and full decoding. The geometric
profile, tile order, quantization and LOD tree do not change.

A stream-only pack must declare streams and q96Header, and must not declare
encodings. Descriptors for it contain only the signed base/SH URLs; no canonical
URL is signed or fetched. Missing/corrupt streams are errors. Updated viewers
are required for these outputs. The current viewers still accept historical
canonical-only or canonical-plus-stream bundles; absence of storage denotes
that historical physical-file contract.


The stream metadata (also supported additively on historical bundles) is:

~~~json
"streams": {
  "version": 1,
  "base": {"path": "packs/example.gst.base", "byteLength": 68, "sha256": "<64 lowercase hex>"},
  "sh": {"path": "packs/example.gst.sh", "byteLength": 92, "sha256": "<64 lowercase hex>"}
}
~~~

The example lengths describe one record. Both files contain all records of the
canonical pack in exactly the same order. A tile starting at canonical byte
offset O uses records starting at (O - 32) / 96 in either stream; tile
quantization and source degrees are unchanged.

Each file has a 32-byte little-endian header:

| Offset | Type | Value |
|---|---|---|
| 0 | 8 bytes | ASCII GSATTR1 followed by NUL |
| 8 | uint16 | version 1 |
| 10 | uint16 | header size 32 |
| 12 | uint16 | stride 36 (base) or 60 (SH) |
| 14 | uint16 | kind 1 (base) or 2 (SH) |
| 16 | uint32 | canonical pack record count |
| 20 | uint64 | reserved, zero |
| 28 | uint32 | CRC32 of payload |

Base record bytes 0..27 copy Q96 bytes 0..27; bytes 28..35 copy Q96
bytes 88..95. This retains position, scale, rotation, opacity logit, colour DC
and uint64 source identifier. SH record bytes 0..59 copy Q96 bytes 28..87:
45 colour coefficients followed by 15 directional-opacity coefficients.
Concatenating the corresponding fields reconstructs the canonical payload
byte-for-byte, without requantization.

A progressive viewer may render base with **zero SH residuals temporarily**.
DC and base opacity are always preserved. The full-quality result must restore
all advertised coefficients. This temporary approximation is distinct from
dropping opacity SH in a full-quality decoder.

SHA256 authenticates each complete stream independently; CRC, kind, count,
stride, reserved bytes and exact length must also validate before use.
Malformed advertised streams are errors. Relative paths use the same containment
rules as canonical packs and base/SH paths must differ. API descriptors bind
both signed URLs to manifest sizes/hashes after tenant-scoped workspace lookup.
A historical bundle can fall back to its canonical pack when stream URLs
were not negotiated. A stream-only bundle cannot fall back and rejects a
descriptor that omits its signed streams. A stream-aware client must not silently accept a
corrupt stream by falling back to other bytes.

The bundler writes only the two raw attribute streams. It neither writes nor
compresses discarded historical packs. Base transfers 36/96 of the Q96 record
bytes (62.5% less, excluding headers); full base+SH still uses 96 bytes per
record, plus two 32-byte headers per aggregate. stats fields
attributeStreamBytes and storedPackBytes measure actual stream bytes;
packBytes retains its virtual Q96 accounting meaning for existing consumers.
There is no canonical compatibility copy. These streams are not compressed;
no bandwidth advantage over a complete historical Zstd file is asserted.

For an existing immutable bundle:

~~~bash
python3 tools/split_gstile_attributes.py /path/to/gstile /path/to/gstile-streams
~~~

The converter verifies all source packs, writes only the two streams, assigns
a new bundle ID and publishes its manifest after all files are complete.
It preserves canonical **payload bytes**, not historical files. Sources may
themselves use canonical or stream-only storage. Output streams are flushed
and read back to verify SHA256. The destination must be new and outside the
source, which is never edited. An interrupted .partial directory remains.
Use --resume to reverify completed files and repair incomplete writes; unknown
files prevent publication. --workers controls parallel I/O (1..16, default 4).
The pipeline publishes the whole new output workspace.
