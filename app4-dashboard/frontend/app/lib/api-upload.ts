import { api, ApiError } from "./api-client";
import { fileResume, UploadResumeMismatch } from "./upload-resume";
import {
  parseDirectUploadSession,
  parseSignedUploadPart,
  parseUploadAbort,
  parseUploadFileCompletion,
  parseUploadResult,
  type DirectUploadFile,
  type DirectUploadSession,
} from "./upload-api-contracts";

const retryable = (error: unknown) => error instanceof TypeError ||
  (error instanceof ApiError && (error.status === 408 || error.status === 429 || error.status >= 500));

const pause = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  signal.throwIfAborted();
  const abort = () => { clearTimeout(timer); reject(signal.reason); };
  const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
  signal.addEventListener("abort", abort, { once: true });
});

const settleWorkers = async (count: number, worker: () => Promise<void>, controller: AbortController) => {
  let failure: unknown;
  const results = await Promise.allSettled(Array.from({ length: count }, async () => {
    try { await worker(); } catch (error) {
      if (!controller.signal.aborted) { failure = error; controller.abort(error); }
      throw error;
    }
  }));
  const rejected = results.find((result) => result.status === "rejected");
  if (rejected?.status === "rejected") throw failure ?? rejected.reason;
};

type UploadedPart = { part_number: number; etag: string };

const jsonRequest = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: body === undefined ? undefined : { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const uploadPart = async (
  sessionId: string,
  descriptor: DirectUploadFile,
  partNumber: number,
  body: Blob,
  signal: AbortSignal,
): Promise<UploadedPart> => {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    signal.throwIfAborted();
    try {
      const signed = await api(
        `/datasets/upload-sessions/${encodeURIComponent(sessionId)}`
          + `/files/${encodeURIComponent(descriptor.file_id)}/parts/${partNumber}`,
        parseSignedUploadPart,
        { ...jsonRequest(), signal },
      );
      if (body.size !== signed.expected_size) {
        throw new Error("Upload part size does not match the signed intent");
      }
      const response = await fetch(signed.url, {
        method: signed.method,
        body,
        credentials: "omit",
        signal,
      });
      if (!response.ok) {
        throw new ApiError(response.status, `S3 upload part failed: HTTP ${response.status}`, Number(response.headers.get("Retry-After") || 0) * 1000);
      }
      const etag = response.headers.get("ETag");
      if (!etag) {
        throw new Error(
          "S3 did not expose the ETag response header; check bucket CORS",
        );
      }
      return { part_number: partNumber, etag };
    } catch (error) {
      if (signal.aborted || !retryable(error)) throw error;
      if (attempt < 3) await pause(Math.max(
        error instanceof ApiError ? error.retryAfterMs || 0 : 0,
        250 * 2 ** (attempt - 1) + Math.random() * 250,
      ), signal);
      lastError = error instanceof Error
        ? error
        : new Error("Upload part failed");
    }
  }
  throw lastError ?? new Error("Upload part failed");
};

const uploadDirectFile = async (
  session: DirectUploadSession,
  descriptor: DirectUploadFile,
  file: File,
  controller: AbortController,
) => {
  const { signal } = controller;
  signal.throwIfAborted();
  const resume = await fileResume(session.session_id, descriptor, file, session.part_size, signal);
  if (descriptor.status === "completed") return;
  const parts = new Array<UploadedPart>(descriptor.total_parts);
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= descriptor.total_parts) {
      signal.throwIfAborted();
      const partNumber = nextPart++;
      const start = (partNumber - 1) * session.part_size;
      const body = file.slice(
        start,
        Math.min(start + session.part_size, file.size),
      );
      const existing = await resume.reusable(partNumber, body);
      if (existing) { parts[partNumber - 1] = existing; continue; }
      parts[partNumber - 1] = await uploadPart(
        session.session_id,
        descriptor,
        partNumber,
        body,
        signal,
      );
      await resume.remember(parts[partNumber - 1], body);
    }
  };
  await settleWorkers(Math.min(4, descriptor.total_parts), worker, controller);
  signal.throwIfAborted();
  await api(
    `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}`
      + `/files/${encodeURIComponent(descriptor.file_id)}/complete`,
    parseUploadFileCompletion,
    { ...jsonRequest({ parts }), signal },
  );
};

export const uploadDataset = async (
  datasetName: string,
  files: FileList,
  onProgress?: (progress: {
    total: number;
    completed: number;
    failed: number;
  }) => void,
) => {
  const total = files.length;
  onProgress?.({ total, completed: 0, failed: 0 });
  let sessionId: string | null = null;
  let completed = 0;
  const controller = new AbortController();
  try {
    const localFiles = Array.from(files);
    const session = await api(
      "/datasets/upload-sessions",
      parseDirectUploadSession,
      jsonRequest({
        dataset_name: datasetName,
        files: localFiles.map((file) => ({
          name: file.name,
          size: file.size,
          content_type: file.type || "application/octet-stream",
        })),
      }),
    );
    sessionId = session.session_id;
    const filesByName = new Map(localFiles.map((file) => [file.name, file]));
    let nextFile = 0;
    const worker = async () => {
      while (nextFile < session.files.length) {
        controller.signal.throwIfAborted();
        const descriptor = session.files[nextFile++];
        const file = filesByName.get(descriptor.name);
        if (!file || file.size !== descriptor.size) {
          throw new Error(
            `Local file changed after upload initialization: ${descriptor.name}`,
          );
        }
        await uploadDirectFile(session, descriptor, file, controller);
        completed += 1;
        onProgress?.({ total, completed, failed: 0 });
      }
    };
    // One file at a time, four parts globally: a bounded browser/network budget.
    await settleWorkers(1, worker, controller);
    controller.signal.throwIfAborted();
    const result = await api(
      `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}/complete`,
      parseUploadResult,
      { ...jsonRequest(), signal: controller.signal },
    );
    onProgress?.({ total, completed: result.completed, failed: result.failed });
    return result;
  } catch (error) {
    controller.abort(error);
    // Transient failures retain the durable session. Repeating the same upload
    // request resumes completed files through the backend's idempotent contract.
    if (sessionId && !retryable(error) && !(error instanceof UploadResumeMismatch)) {
      await api(
        `/datasets/upload-sessions/${encodeURIComponent(sessionId)}`,
        parseUploadAbort,
        { method: "DELETE" },
      ).catch(() => undefined);
    }
    const message = error instanceof Error ? error.message : "network error";
    const results = Array.from(files, (file) => ({
      name: file.name,
      status: "error",
      error: message,
    }));
    onProgress?.({ total, completed, failed: total - completed });
    return {
      total,
      completed,
      failed: total - completed,
      session_id: sessionId,
      resumable: retryable(error),
      status: "partial",
      files: results,
    };
  }
};
