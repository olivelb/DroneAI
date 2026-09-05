import type { GsTilePack } from "./contracts";
import { crc32 } from "./decode";

/** Inputs have independent manifest SHA identities; CRC/header also bind kind and count. */
export function validateAttributeStream(content: ArrayBuffer, kind: "base" | "sh", count: number) {
  const stride = kind === "base" ? 36 : 60;
  if (content.byteLength !== 32 + count * stride) throw new Error("GSTile stream length mismatch");
  const view = new DataView(content);
  if (String.fromCharCode(...new Uint8Array(content, 0, 8)) !== "GSATTR1\0" ||
      view.getUint16(8, true) !== 1 || view.getUint16(10, true) !== 32 ||
      view.getUint16(12, true) !== stride || view.getUint16(14, true) !== (kind === "base" ? 1 : 2) ||
      view.getUint32(16, true) !== count || view.getBigUint64(20, true) !== BigInt(0) ||
      view.getUint32(28, true) !== crc32(new Uint8Array(content, 32)))
    throw new Error("GSTile stream header or CRC mismatch");
}

/** Reconstitute only the requested tile for the existing worker/GPU decoder. */
export function assembleAttributeTile(base: ArrayBuffer, sh: ArrayBuffer | null,
  pack: Pick<GsTilePack, "recordCount">, first: number, count: number) {
  if (!Number.isSafeInteger(first) || !Number.isSafeInteger(count) || first < 0 || count < 1 || first + count > pack.recordCount)
    throw new Error("GSTile attribute tile range escapes pack");
  const result = new ArrayBuffer(32 + count * 96);
  const out = new Uint8Array(result);
  out.set(new TextEncoder().encode("GSTILE1\0"));
  const header = new DataView(result);
  header.setUint16(8, 1, true); header.setUint16(10, 32, true);
  header.setUint16(12, 96, true); header.setUint32(16, count, true);
  const geometry = new Uint8Array(base), harmonics = sh ? new Uint8Array(sh) : null;
  if (base.byteLength !== 32 + pack.recordCount * 36 || (sh && sh.byteLength !== 32 + pack.recordCount * 60))
    throw new Error("GSTile attribute buffer length mismatch");
  for (let i = 0; i < count; i++) {
    const src = 32 + (first + i) * 36, dst = 32 + i * 96;
    out.set(geometry.subarray(src, src + 28), dst);
    out.set(geometry.subarray(src + 28, src + 36), dst + 88);
    if (harmonics) out.set(harmonics.subarray(32 + (first + i) * 60, 32 + (first + i + 1) * 60), dst + 28);
  }
  // SHA-verified decoder intentionally does not repeat CRC on this in-memory tile.
  return result;
}
