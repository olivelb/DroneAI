import { afterEach, expect, it, vi } from "vitest";
import { fileResume, UploadResumeMismatch } from "./upload-resume";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("reuses only parts with both matching local bytes and provider ETags", async () => {
  const saved = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
    removeItem: (key: string) => saved.delete(key),
  });
  const file = new File(["abcdef"], "image.jpg", { lastModified: 10 });
  const descriptor = { file_id: "f", name: "image.jpg", size: 6, s3_key: "file", total_parts: 2, status: "uploading" };
  const signal = new AbortController().signal;
  const first = await fileResume("session", descriptor, file, 3, signal);
  await first.remember({ part_number: 1, etag: "etag-one" }, file.slice(0, 3));
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    { part_number: 1, etag: "etag-one", size: 3 },
  ]), { status: 200 })));
  const resumed = await fileResume("session", descriptor, file, 3, signal);
  expect(await resumed.reusable(1, file.slice(0, 3))).toEqual({ part_number: 1, etag: "etag-one" });
  expect(await resumed.reusable(1, new Blob(["xyz"]))).toBeNull();
  expect(await resumed.reusable(2, file.slice(3))).toBeNull();
  await expect(fileResume("session", descriptor, new File(["abcdef"], "image.jpg", { lastModified: 11 }), 3, signal)).rejects.toBeInstanceOf(UploadResumeMismatch);
});
