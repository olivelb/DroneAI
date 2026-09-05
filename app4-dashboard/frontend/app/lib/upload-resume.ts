import { api } from "./api-client";
import { parseUploadedParts, type DirectUploadFile } from "./upload-api-contracts";

type Part = { part_number: number; etag: string };
type Record = { fingerprint: string; parts: { [number: string]: Part & { sha256: string } } };
const keyFor = (session: string, file: string) => `droneai:upload:${session}:${file}`;
const fingerprint = (file: File) => JSON.stringify([file.name, file.size, file.lastModified]);
export class UploadResumeMismatch extends Error {}

// A versioned SHA-256 tree fingerprint keeps memory bounded even for a very
// large multipart part. It is a local resume identity, not an S3 checksum.
const digest = async (body: Blob) => {
  const hashes: string[] = [];
  const hex = (buffer: ArrayBuffer) => Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
  for (let offset = 0; offset < body.size; offset += 1024 * 1024) {
    hashes.push(hex(await crypto.subtle.digest("SHA-256", await body.slice(offset, offset + 1024 * 1024).arrayBuffer())));
  }
  return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(["sha256-tree-v1", body.size, hashes]))));
};

export async function fileResume(session: string, descriptor: DirectUploadFile, file: File, partSize: number, signal: AbortSignal) {
  const key = keyFor(session, descriptor.file_id);
  let saved: Record = { fingerprint: fingerprint(file), parts: {} };
  let found = false;
  try {
    const raw = sessionStorage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw) as Record;
      if (parsed.fingerprint !== fingerprint(file)) throw new UploadResumeMismatch("Select the original unchanged files to resume this upload");
      if (parsed.parts && typeof parsed.parts === "object") { saved = parsed; found = true; }
    }
  } catch (error) {
    if (error instanceof UploadResumeMismatch) throw error;
  }
  const confirmed = found && descriptor.status !== "completed"
    ? await api(`/datasets/upload-sessions/${encodeURIComponent(session)}/files/${encodeURIComponent(descriptor.file_id)}/parts`, parseUploadedParts, { signal }) : [];
  const reusable = async (number: number, body: Blob): Promise<Part | null> => {
    signal.throwIfAborted();
    const previous = saved.parts[number];
    if (!previous || previous.sha256 !== await digest(body)) return null;
    const remote = confirmed.find((part) => part.part_number === number && part.etag === previous.etag && part.size === body.size);
    return remote || descriptor.status === "completed" ? { part_number: number, etag: previous.etag } : null;
  };
  if (descriptor.status === "completed") {
    for (let number = 1; number <= descriptor.total_parts; number++) {
      if (!await reusable(number, file.slice((number - 1) * partSize, number * partSize))) {
        throw new UploadResumeMismatch("Cannot verify the original completed file; select the original files in the original browser tab");
      }
    }
  }
  return {
    reusable,
    async remember(part: Part, body: Blob) {
      saved.parts[part.part_number] = { ...part, sha256: await digest(body) };
      try { sessionStorage.setItem(key, JSON.stringify(saved)); } catch { /* Upload still works without browser persistence. */ }
    },
    clear() { try { sessionStorage.removeItem(key); } catch { /* Persistence may be unavailable. */ } },
  };
}
