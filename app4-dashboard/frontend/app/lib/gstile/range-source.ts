import type { GsTilePersistentCache } from "./persistent-range-cache";

export type ByteRange = { start: number; length: number };
export type GsTileNetworkTransport = {
  url: string;
  byteLength: number;
  encoding: "identity" | "zstd" | "zstd-http";
  fallbackUrl?: string;
};
export type GsTileRangePriority = "critical" | "prefetch";

export const DEFAULT_GSTILE_MEMORY_CACHE_BYTES = 768 * 1024 * 1024;
export const DEFAULT_GSTILE_ORPHAN_GRACE_MILLISECONDS = 300;

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
  allowFullObjectResponse = false,
  responseByteLength = range.length,
  requestRange = true,
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
    ...(requestRange
      ? { headers: { Range: `bytes=${range.start}-${end}` } }
      : {}),
    signal,
    credentials: "same-origin",
  });
  const fullObjectResponse =
    allowFullObjectResponse && range.start === 0 && response.status === 200;
  if (response.status !== 206 && !fullObjectResponse) {
    throw new GsTileRangeError(
      `GSTile range request returned HTTP ${response.status}`,
    );
  }
  if (!fullObjectResponse) {
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
  }
  const content = await response.arrayBuffer();
  if (content.byteLength !== responseByteLength) {
    throw new GsTileRangeError("GSTile range response length mismatch");
  }
  return content;
};

const createZstdDecompressionStream = () =>
  new DecompressionStream("zstd" as CompressionFormat);

let zstdSupported: boolean | undefined;

export const supportsGsTileZstd = () => {
  if (zstdSupported !== undefined) return zstdSupported;
  if (typeof globalThis.DecompressionStream !== "function") {
    zstdSupported = false;
    return false;
  }
  try {
    createZstdDecompressionStream();
    zstdSupported = true;
  } catch {
    zstdSupported = false;
  }
  return zstdSupported;
};

const decompressZstd = async (
  content: ArrayBuffer,
  expectedByteLength: number,
) => {
  const stream = new Blob([content])
    .stream()
    .pipeThrough(createZstdDecompressionStream());
  const decoded = await new Response(stream).arrayBuffer();
  if (decoded.byteLength !== expectedByteLength) {
    throw new GsTileRangeError("GSTile zstd decoded length mismatch");
  }
  return decoded;
};

export type GsTileRangeSchedulerStatistics = {
  active: number;
  queued: number;
  queuedCritical: number;
  queuedPrefetch: number;
  priorityPromotions: number;
  prefetchCancellations: number;
  criticalQueueWaits: number;
  criticalQueueWaitMilliseconds: number;
  maximumCriticalQueueWaitMilliseconds: number;
  cacheEntries: number;
  cacheBytes: number;
  cacheHits: number;
  cacheMisses: number;
  inFlightHits: number;
  persistentCacheHits: number;
  persistentCacheMisses: number;
  persistentWrites: number;
  persistentWriteBytes: number;
  persistentErrors: number;
  networkBytes: number;
  decodedBytes: number;
  zstdResponses: number;
  zstdFallbacks: number;
};

type InFlightRange = {
  promise: Promise<ArrayBuffer>;
  controller: AbortController;
  consumers: number;
  settled: boolean;
  abortTimer: ReturnType<typeof setTimeout> | null;
  orphanGraceMilliseconds: number;
  priority: GsTileRangePriority;
  promote: (() => void) | null;
};

type QueueEntry = {
  resume: () => void;
  reject: (reason?: unknown) => void;
  signal?: AbortSignal;
  abort?: () => void;
  priority: GsTileRangePriority;
};

export class GsTileRangeScheduler {
  readonly #maximumConcurrency: number;
  readonly #maximumCacheBytes: number;
  readonly #orphanGraceMilliseconds: number;
  readonly #persistentCache: GsTilePersistentCache | null;
  #active = 0;
  #queue: QueueEntry[] = [];
  #cache = new Map<string, ArrayBuffer>();
  #cacheBytes = 0;
  #cacheHits = 0;
  #cacheMisses = 0;
  #inFlightHits = 0;
  #persistentCacheHits = 0;
  #persistentCacheMisses = 0;
  #persistentWrites = 0;
  #persistentWriteBytes = 0;
  #persistentErrors = 0;
  #networkBytes = 0;
  #decodedBytes = 0;
  #zstdResponses = 0;
  #zstdFallbacks = 0;
  #priorityPromotions = 0;
  #prefetchCancellations = 0;
  #criticalQueueWaits = 0;
  #criticalQueueWaitMilliseconds = 0;
  #maximumCriticalQueueWaitMilliseconds = 0;
  #inFlight = new Map<string, InFlightRange>();
  #persistentBuffers = new WeakSet<ArrayBuffer>();

  constructor(
    maximumConcurrency = 8,
    maximumCacheBytes = DEFAULT_GSTILE_MEMORY_CACHE_BYTES,
    orphanGraceMilliseconds = DEFAULT_GSTILE_ORPHAN_GRACE_MILLISECONDS,
    persistentCache: GsTilePersistentCache | null = null,
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
    this.#persistentCache = persistentCache;
  }

  statistics(): GsTileRangeSchedulerStatistics {
    return {
      active: this.#active,
      queued: this.#queue.length,
      queuedCritical: this.#queue.filter(
        (entry) => entry.priority === "critical",
      ).length,
      queuedPrefetch: this.#queue.filter(
        (entry) => entry.priority === "prefetch",
      ).length,
      priorityPromotions: this.#priorityPromotions,
      prefetchCancellations: this.#prefetchCancellations,
      criticalQueueWaits: this.#criticalQueueWaits,
      criticalQueueWaitMilliseconds: this.#criticalQueueWaitMilliseconds,
      maximumCriticalQueueWaitMilliseconds:
        this.#maximumCriticalQueueWaitMilliseconds,
      cacheEntries: this.#cache.size,
      cacheBytes: this.#cacheBytes,
      cacheHits: this.#cacheHits,
      cacheMisses: this.#cacheMisses,
      inFlightHits: this.#inFlightHits,
      persistentCacheHits: this.#persistentCacheHits,
      persistentCacheMisses: this.#persistentCacheMisses,
      persistentWrites: this.#persistentWrites,
      persistentWriteBytes: this.#persistentWriteBytes,
      persistentErrors: this.#persistentErrors,
      networkBytes: this.#networkBytes,
      decodedBytes: this.#decodedBytes,
      zstdResponses: this.#zstdResponses,
      zstdFallbacks: this.#zstdFallbacks,
    };
  }

  hasLocallyAvailable(
    url: string,
    range: ByteRange,
    immutableIdentity?: string,
  ) {
    const key = this.#rangeKey(url, range, immutableIdentity);
    return this.#cache.has(key) || this.#inFlight.has(key);
  }

  async fetch(
    url: string,
    range: ByteRange,
    signal?: AbortSignal,
    immutableIdentity?: string,
    transport?: GsTileNetworkTransport,
    priority: GsTileRangePriority = "critical",
  ) {
    if (signal?.aborted) return Promise.reject(signal.reason);
    const key = this.#rangeKey(url, range, immutableIdentity);
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
      if (priority === "critical" && request.priority === "prefetch") {
        request.priority = "critical";
        request.orphanGraceMilliseconds = this.#orphanGraceMilliseconds;
        this.#priorityPromotions += 1;
        request.promote?.();
      }
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
      orphanGraceMilliseconds:
        priority === "prefetch" ? 0 : this.#orphanGraceMilliseconds,
      priority,
      promote: null,
    };
    const entry = request;
    entry.promise = this.#fetchAndCache(
      key,
      url,
      range,
      controller.signal,
      entry,
      transport,
    ).finally(() => {
        entry.settled = true;
        if (entry.abortTimer !== null) clearTimeout(entry.abortTimer);
        entry.abortTimer = null;
        if (this.#inFlight.get(key) === entry) this.#inFlight.delete(key);
      },
    );
    this.#inFlight.set(key, request);
    return this.#subscribe(request, signal);
  }

  persistVerified(
    immutableIdentity: string,
    range: ByteRange,
    content: ArrayBuffer,
  ) {
    if (!this.#persistentCache || this.#persistentBuffers.has(content)) return;
    if (content.byteLength !== range.length) {
      throw new Error("Verified GSTile range length mismatch");
    }
    const key = this.#rangeKey("", range, immutableIdentity);
    this.#persistentBuffers.add(content);
    void this.#persistentCache.write(key, content).then(
      () => {
        this.#persistentWrites += 1;
        this.#persistentWriteBytes += content.byteLength;
      },
      () => {
        this.#persistentErrors += 1;
      },
    );
  }

  evictPersistent(immutableIdentity: string, range: ByteRange) {
    if (!this.#persistentCache) return;
    const key = this.#rangeKey("", range, immutableIdentity);
    void this.#persistentCache.delete(key).catch(() => {
      this.#persistentErrors += 1;
    });
  }

  async #fetchAndCache(
    key: string,
    url: string,
    range: ByteRange,
    signal: AbortSignal,
    request: InFlightRange,
    transport?: GsTileNetworkTransport,
  ) {
    await this.#acquire(signal, request);
    try {
      if (this.#persistentCache && key.startsWith("immutable:")) {
        try {
          const persistent = await this.#persistentCache.read(
            key,
            range.length,
            signal,
          );
          if (persistent) {
            this.#persistentCacheHits += 1;
            this.#persistentBuffers.add(persistent);
            this.#putMemory(key, persistent);
            return persistent;
          }
          this.#persistentCacheMisses += 1;
        } catch (error) {
          if (signal.aborted) throw error;
          this.#persistentErrors += 1;
        }
      }
      const networkRange = transport
        ? { start: 0, length: transport.byteLength }
        : range;
      let encoded: ArrayBuffer;
      let content: ArrayBuffer;
      try {
        encoded = await fetchGsTileRange(
          transport?.url ?? url,
          networkRange,
          signal,
          transport !== undefined,
          transport?.encoding === "zstd-http"
            ? range.length
            : networkRange.length,
          transport?.encoding !== "zstd-http",
        );
        content =
          transport?.encoding === "zstd"
            ? await decompressZstd(encoded, range.length)
            : encoded;
        if (content.byteLength !== range.length) {
          throw new GsTileRangeError("GSTile decoded range length mismatch");
        }
      } catch (error) {
        if (
          signal.aborted ||
          (transport?.encoding !== "zstd" &&
            transport?.encoding !== "zstd-http") ||
          !transport.fallbackUrl
        ) {
          throw error;
        }
        this.#zstdFallbacks += 1;
        encoded = await fetchGsTileRange(
          transport.fallbackUrl,
          range,
          signal,
          true,
        );
        content = encoded;
        transport = undefined;
      }
      this.#networkBytes +=
        transport?.encoding === "zstd-http"
          ? transport.byteLength
          : encoded.byteLength;
      this.#decodedBytes += content.byteLength;
      if (transport?.encoding === "zstd" || transport?.encoding === "zstd-http") {
        this.#zstdResponses += 1;
      }
      this.#putMemory(key, content);
      return content;
    } finally {
      this.#release();
    }
  }

  #rangeKey(
    url: string,
    range: ByteRange,
    immutableIdentity?: string,
  ) {
    if (immutableIdentity !== undefined && !immutableIdentity) {
      throw new Error("GSTile immutable cache identity cannot be empty");
    }
    const source = immutableIdentity
      ? `immutable:${immutableIdentity}`
      : `url:${url}`;
    return `${source}\0${range.start}\0${range.length}`;
  }

  #putMemory(key: string, content: ArrayBuffer) {
    if (content.byteLength > this.#maximumCacheBytes) return;
    const existing = this.#cache.get(key);
    if (existing) {
      this.#cache.delete(key);
      this.#cacheBytes -= existing.byteLength;
    }
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
            if (request.priority === "prefetch") {
              this.#prefetchCancellations += 1;
            }
            request.controller.abort(
              new DOMException("Superseded GSTile range", "AbortError"),
            );
          }
        }, request.orphanGraceMilliseconds);
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

  #enqueue(entry: QueueEntry) {
    if (entry.priority === "prefetch") {
      this.#queue.push(entry);
      return;
    }
    const firstPrefetch = this.#queue.findIndex(
      (candidate) => candidate.priority === "prefetch",
    );
    if (firstPrefetch < 0) this.#queue.push(entry);
    else this.#queue.splice(firstPrefetch, 0, entry);
  }

  #acquire(signal: AbortSignal | undefined, request: InFlightRange): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (this.#active < this.#maximumConcurrency) {
      this.#active += 1;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const queuedAt = performance.now();
      const queued: QueueEntry = {
        resume: () => {
          signal?.removeEventListener("abort", queued.abort!);
          request.promote = null;
          if (queued.priority === "critical") {
            const queueWaitMilliseconds = performance.now() - queuedAt;
            this.#criticalQueueWaits += 1;
            this.#criticalQueueWaitMilliseconds += queueWaitMilliseconds;
            this.#maximumCriticalQueueWaitMilliseconds = Math.max(
              this.#maximumCriticalQueueWaitMilliseconds,
              queueWaitMilliseconds,
            );
          }
          this.#active += 1;
          resolve();
        },
        reject,
        signal,
        priority: request.priority,
      };
      queued.abort = () => {
        const index = this.#queue.indexOf(queued);
        if (index >= 0) this.#queue.splice(index, 1);
        request.promote = null;
        reject(signal?.reason);
      };
      request.promote = () => {
        const index = this.#queue.indexOf(queued);
        if (index < 0 || queued.priority === "critical") return;
        this.#queue.splice(index, 1);
        queued.priority = "critical";
        this.#enqueue(queued);
      };
      signal?.addEventListener("abort", queued.abort, { once: true });
      this.#enqueue(queued);
    });
  }

  #release() {
    this.#active -= 1;
    this.#queue.shift()?.resume();
  }
}
