export type ByteRange = { start: number; length: number };

export class GsTileRangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GsTileRangeError";
  }
}

const expectedContentRange = (range: ByteRange, total: number | null) => {
  const end = range.start + range.length - 1;
  return `bytes ${range.start}-${end}/${total ?? "*"}`;
};

export const fetchGsTileRange = async (
  url: string,
  range: ByteRange,
  signal?: AbortSignal,
): Promise<ArrayBuffer> => {
  if (
    !Number.isSafeInteger(range.start) ||
    !Number.isSafeInteger(range.length) ||
    range.start < 0 ||
    range.length < 1
  ) {
    throw new GsTileRangeError("Invalid GSTile byte range");
  }
  const end = range.start + range.length - 1;
  const response = await fetch(url, {
    headers: { Range: `bytes=${range.start}-${end}` },
    signal,
    credentials: "same-origin",
  });
  if (response.status !== 206) {
    throw new GsTileRangeError(
      `GSTile range request returned HTTP ${response.status}`,
    );
  }
  const contentRange = response.headers.get("content-range");
  const match = contentRange?.match(/^bytes (\d+)-(\d+)\/(\d+|\*)$/);
  if (
    !match ||
    Number(match[1]) !== range.start ||
    Number(match[2]) !== end
  ) {
    throw new GsTileRangeError(
      `Invalid Content-Range; expected ${expectedContentRange(range, null)}`,
    );
  }
  const content = await response.arrayBuffer();
  if (content.byteLength !== range.length) {
    throw new GsTileRangeError("GSTile range response length mismatch");
  }
  return content;
};

export class GsTileRangeScheduler {
  readonly #maximumConcurrency: number;
  #active = 0;
  #queue: Array<() => void> = [];

  constructor(maximumConcurrency = 4) {
    if (!Number.isInteger(maximumConcurrency) || maximumConcurrency < 1) {
      throw new Error("GSTile concurrency must be a positive integer");
    }
    this.#maximumConcurrency = maximumConcurrency;
  }

  async fetch(url: string, range: ByteRange, signal?: AbortSignal) {
    await this.#acquire(signal);
    try {
      return await fetchGsTileRange(url, range, signal);
    } finally {
      this.#release();
    }
  }

  #acquire(signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (this.#active < this.#maximumConcurrency) {
      this.#active += 1;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const resume = () => {
        signal?.removeEventListener("abort", abort);
        this.#active += 1;
        resolve();
      };
      const abort = () => {
        const index = this.#queue.indexOf(resume);
        if (index >= 0) this.#queue.splice(index, 1);
        reject(signal?.reason);
      };
      signal?.addEventListener("abort", abort, { once: true });
      this.#queue.push(resume);
    });
  }

  #release() {
    this.#active -= 1;
    this.#queue.shift()?.();
  }
}
