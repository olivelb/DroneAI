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

export type GsTileRangeSchedulerStatistics = {
  active: number;
  queued: number;
  cacheEntries: number;
  cacheBytes: number;
  cacheHits: number;
  cacheMisses: number;
  inFlightHits: number;
};

type InFlightRange = {
  promise: Promise<ArrayBuffer>;
  controller: AbortController;
  consumers: number;
  settled: boolean;
  abortTimer: ReturnType<typeof setTimeout> | null;
};

type QueueEntry = {
  resume: () => void;
  reject: (reason?: unknown) => void;
  signal?: AbortSignal;
  abort?: () => void;
};

export class GsTileRangeScheduler {
  readonly #maximumConcurrency: number;
  readonly #maximumCacheBytes: number;
  readonly #orphanGraceMilliseconds: number;
  #active = 0;
  #queue: QueueEntry[] = [];
  #cache = new Map<string, ArrayBuffer>();
  #cacheBytes = 0;
  #cacheHits = 0;
  #cacheMisses = 0;
  #inFlightHits = 0;
  #inFlight = new Map<string, InFlightRange>();

  constructor(
    maximumConcurrency = 8,
    maximumCacheBytes = 768 * 1024 * 1024,
    orphanGraceMilliseconds = 300,
  ) {
    if (!Number.isInteger(maximumConcurrency) || maximumConcurrency < 1) {
      throw new Error("GSTile concurrency must be a positive integer");
    }
    if (!Number.isSafeInteger(maximumCacheBytes) || maximumCacheBytes < 0) {
      throw new Error("GSTile cache size must be a non-negative integer");
    }
    if (
      !Number.isSafeInteger(orphanGraceMilliseconds) ||
      orphanGraceMilliseconds < 0
    ) {
      throw new Error("GSTile orphan grace must be a non-negative integer");
    }
    this.#maximumConcurrency = maximumConcurrency;
    this.#maximumCacheBytes = maximumCacheBytes;
    this.#orphanGraceMilliseconds = orphanGraceMilliseconds;
  }

  statistics(): GsTileRangeSchedulerStatistics {
    return {
      active: this.#active,
      queued: this.#queue.length,
      cacheEntries: this.#cache.size,
      cacheBytes: this.#cacheBytes,
      cacheHits: this.#cacheHits,
      cacheMisses: this.#cacheMisses,
      inFlightHits: this.#inFlightHits,
    };
  }

  async fetch(url: string, range: ByteRange, signal?: AbortSignal) {
    if (signal?.aborted) return Promise.reject(signal.reason);
    const key = `${url}\0${range.start}\0${range.length}`;
    const cached = this.#cache.get(key);
    if (cached) {
      this.#cache.delete(key);
      this.#cache.set(key, cached);
      this.#cacheHits += 1;
      if (signal?.aborted) return Promise.reject(signal.reason);
      return cached;
    }
    let request = this.#inFlight.get(key);
    if (request) {
      this.#inFlightHits += 1;
      return this.#subscribe(request, signal);
    }
    this.#cacheMisses += 1;
    const controller = new AbortController();
    request = {
      promise: Promise.resolve(new ArrayBuffer(0)),
      controller,
      consumers: 0,
      settled: false,
      abortTimer: null,
    };
    const entry = request;
    entry.promise = this.#fetchAndCache(key, url, range, controller.signal).finally(
      () => {
        entry.settled = true;
        if (entry.abortTimer !== null) clearTimeout(entry.abortTimer);
        entry.abortTimer = null;
        if (this.#inFlight.get(key) === entry) this.#inFlight.delete(key);
      },
    );
    this.#inFlight.set(key, request);
    return this.#subscribe(request, signal);
  }

  async #fetchAndCache(
    key: string,
    url: string,
    range: ByteRange,
    signal: AbortSignal,
  ) {
    await this.#acquire(signal);
    try {
      const content = await fetchGsTileRange(url, range, signal);
      if (content.byteLength <= this.#maximumCacheBytes) {
        while (
          this.#cacheBytes + content.byteLength > this.#maximumCacheBytes &&
          this.#cache.size > 0
        ) {
          const oldest = this.#cache.entries().next().value as
            | [string, ArrayBuffer]
            | undefined;
          if (!oldest) break;
          this.#cache.delete(oldest[0]);
          this.#cacheBytes -= oldest[1].byteLength;
        }
        this.#cache.set(key, content);
        this.#cacheBytes += content.byteLength;
      }
      return content;
    } finally {
      this.#release();
    }
  }

  #subscribe(request: InFlightRange, signal?: AbortSignal) {
    if (request.abortTimer !== null) {
      clearTimeout(request.abortTimer);
      request.abortTimer = null;
    }
    request.consumers += 1;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      request.consumers -= 1;
      if (request.consumers === 0 && !request.settled) {
        request.abortTimer = setTimeout(() => {
          request.abortTimer = null;
          if (request.consumers === 0 && !request.settled) {
            request.controller.abort(
              new DOMException("Superseded GSTile range", "AbortError"),
            );
          }
        }, this.#orphanGraceMilliseconds);
      }
    };
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const abort = () => {
        release();
        reject(signal?.reason);
      };
      signal?.addEventListener("abort", abort, { once: true });
      void request.promise.then(
        (content) => {
          signal?.removeEventListener("abort", abort);
          release();
          resolve(content);
        },
        (error: unknown) => {
          signal?.removeEventListener("abort", abort);
          release();
          reject(error);
        },
      );
    });
  }

  #acquire(signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (this.#active < this.#maximumConcurrency) {
      this.#active += 1;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const queued: QueueEntry = {
        resume: () => {
          signal?.removeEventListener("abort", queued.abort!);
          this.#active += 1;
          resolve();
        },
        reject,
        signal,
      };
      queued.abort = () => {
        const index = this.#queue.indexOf(queued);
        if (index >= 0) this.#queue.splice(index, 1);
        reject(signal?.reason);
      };
      signal?.addEventListener("abort", queued.abort, { once: true });
      this.#queue.push(queued);
    });
  }

  #release() {
    this.#active -= 1;
    this.#queue.shift()?.resume();
  }
}
