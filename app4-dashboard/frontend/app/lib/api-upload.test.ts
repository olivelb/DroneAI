import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadDataset } from "./api";

const jsonResponse = (payload: unknown, status = 200) => new Response(
  JSON.stringify(payload),
  { status, headers: { "Content-Type": "application/json" } },
);

const fileList = (name: string, contents: string): FileList => {
  const blob = new Blob([contents], { type: "image/jpeg" });
  Object.defineProperty(blob, "name", { value: name });
  return { 0: blob, length: 1 } as unknown as FileList;
};

describe("uploadDataset", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uploads parts directly to S3 before finalizing the durable session", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/datasets/upload-sessions")) {
        return jsonResponse({
          session_id: "session-1",
          dataset: "quarry",
          status: "uploading",
          total: 1,
          total_bytes: 3,
          part_size: 5 * 1024 * 1024,
          expires_at: "2026-08-09T00:00:00Z",
          files: [{
            file_id: "file-1",
            name: "image.jpg",
            size: 3,
            s3_key: "datasets/quarry/image.jpg",
            total_parts: 1,
            status: "uploading",
          }],
        }, 201);
      }
      if (url.includes("/parts/1")) {
        return jsonResponse({ method: "PUT", url: "https://objects.example/part-1" });
      }
      if (url === "https://objects.example/part-1") {
        expect(init?.credentials).toBe("omit");
        expect(init?.body).toBeInstanceOf(Blob);
        return new Response(null, { status: 200, headers: { ETag: '"etag-1"' } });
      }
      if (url.endsWith("/files/file-1/complete")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          parts: [{ part_number: 1, etag: '"etag-1"' }],
        });
        return jsonResponse({ status: "completed" });
      }
      if (url.endsWith("/upload-sessions/session-1/complete")) {
        return jsonResponse({ total: 1, completed: 1, failed: 0, status: "done" });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    const progress: Array<{ total: number; completed: number; failed: number }> = [];
    const result = await uploadDataset(
      "quarry",
      fileList("image.jpg", "abc"),
      (value) => progress.push(value),
    );

    expect(result).toEqual({ total: 1, completed: 1, failed: 0, status: "done" });
    expect(calls).toEqual([
      "POST http://localhost:30080/datasets/upload-sessions",
      "POST http://localhost:30080/datasets/upload-sessions/session-1/files/file-1/parts/1",
      "PUT https://objects.example/part-1",
      "POST http://localhost:30080/datasets/upload-sessions/session-1/files/file-1/complete",
      "POST http://localhost:30080/datasets/upload-sessions/session-1/complete",
    ]);
    expect(progress.at(-1)).toEqual({ total: 1, completed: 1, failed: 0 });
  });
});
