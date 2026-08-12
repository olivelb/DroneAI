import { api } from "./api-client";

type DirectUploadFile = {
  file_id: string;
  name: string;
  size: number;
  s3_key: string;
  total_parts: number;
  status: string;
};

type DirectUploadSession = {
  session_id: string;
  dataset: string;
  status: string;
  total: number;
  total_bytes: number;
  part_size: number;
  expires_at: string;
  files: DirectUploadFile[];
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
): Promise<UploadedPart> => {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const signed = await api<{ method: string; url: string }>(
        `/datasets/upload-sessions/${encodeURIComponent(sessionId)}`
          + `/files/${encodeURIComponent(descriptor.file_id)}/parts/${partNumber}`,
        jsonRequest(),
      );
      const response = await fetch(signed.url, {
        method: signed.method,
        body,
        credentials: "omit",
      });
      if (!response.ok) {
        throw new Error(`S3 upload part failed: HTTP ${response.status}`);
      }
      const etag = response.headers.get("ETag");
      if (!etag) {
        throw new Error(
          "S3 did not expose the ETag response header; check bucket CORS",
        );
      }
      return { part_number: partNumber, etag };
    } catch (error) {
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
) => {
  const parts = new Array<UploadedPart>(descriptor.total_parts);
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= descriptor.total_parts) {
      const partNumber = nextPart++;
      const start = (partNumber - 1) * session.part_size;
      const body = file.slice(
        start,
        Math.min(start + session.part_size, file.size),
      );
      parts[partNumber - 1] = await uploadPart(
        session.session_id,
        descriptor,
        partNumber,
        body,
      );
    }
  };
  await Promise.all(
    Array.from(
      { length: Math.min(4, descriptor.total_parts) },
      () => worker(),
    ),
  );
  await api(
    `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}`
      + `/files/${encodeURIComponent(descriptor.file_id)}/complete`,
    jsonRequest({ parts }),
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
  try {
    const localFiles = Array.from(files);
    const session = await api<DirectUploadSession>(
      "/datasets/upload-sessions",
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
    let completed = 0;
    let nextFile = 0;
    const worker = async () => {
      while (nextFile < session.files.length) {
        const descriptor = session.files[nextFile++];
        const file = filesByName.get(descriptor.name);
        if (!file || file.size !== descriptor.size) {
          throw new Error(
            `Local file changed after upload initialization: ${descriptor.name}`,
          );
        }
        await uploadDirectFile(session, descriptor, file);
        completed += 1;
        onProgress?.({ total, completed, failed: 0 });
      }
    };
    await Promise.all(
      Array.from(
        { length: Math.min(3, session.files.length) },
        () => worker(),
      ),
    );
    const result = await api<{
      total: number;
      completed: number;
      failed: number;
      status: string;
    }>(
      `/datasets/upload-sessions/${encodeURIComponent(session.session_id)}/complete`,
      jsonRequest(),
    );
    onProgress?.({ total, completed: result.completed, failed: result.failed });
    return result;
  } catch (error) {
    if (sessionId) {
      await api(`/datasets/upload-sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      }).catch(() => undefined);
    }
    const message = error instanceof Error ? error.message : "network error";
    const results = Array.from(files, (file) => ({
      name: file.name,
      status: "error",
      error: message,
    }));
    onProgress?.({ total, completed: 0, failed: total });
    return {
      total,
      completed: 0,
      failed: total,
      status: "partial",
      files: results,
    };
  }
};
