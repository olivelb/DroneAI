import type { GsTilePersistentCache } from "./persistent-range-cache";
import { GsTileMemoryRangeCache } from "./memory-range-cache";

export type ByteRange = { start: number; length: number };
export type GsTileNetworkTransport = {
  url: string;
  byteLength: number;
  encoding: "identity" | "zstd" | "zstd-http";
  fallbackUrl?: string;
};
export type GsTileRangePriority = "critical" | "prefetch";
export type GsTileRangeAvailabilityRequest = {
  id: string;
  url: string;
  range: ByteRange;
  immutableIdentity?: string;
};

export const DEFAULT_GSTILE_MEMORY_CACHE_BYTES = 768 * 1024 * 1024;
/** Explicit desktop experiment; never infer a larger allocation from the browser. */
export const gstileMemoryCacheBytes = (profile?: string | null): number =>
  profile === "desktop"
    ? 1536 * 1024 * 1024
    : DEFAULT_GSTILE_MEMORY_CACHE_BYTES;

export const DEFAULT_GSTILE_ORPHAN_GRACE_MILLISECONDS = 300;
export const DEFAULT_GSTILE_PERSISTENT_READ_CONCURRENCY = 2;

export class GsTileRangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GsTileRangeError";
  }
}

const MAXIMUM_RANGE_RETRIES = 2;
const MAXIMUM_RETRY_DELAY_MS = 2_000;

class GsTileHttpError extends GsTileRangeError {
  constructor(readonly status: number, readonly retryAfter: string | null) {
    super(`GSTile range request returned HTTP ${status}`);
  }
}

const retryDelay = (error: unknown, retry: number): number | null => {
  if (retry >= MAXIMUM_RANGE_RETRIES) return null;
  if (error instanceof GsTileHttpError) {
    if (![408, 429, 500, 502, 503, 504].includes(error.status)) return null;
    if (error.retryAfter !== null) {
      const seconds = /^\d+$/.test(error.retryAfter)
        ? Number(error.retryAfter)
        : (Date.parse(error.retryAfter) - Date.now()) / 1_000;
      if (Number.isFinite(seconds)) {
        // Do not retry earlier than the server asks, or stall a cut indefinitely.
        if (seconds * 1_000 > MAXIMUM_RETRY_DELAY_MS) return null;
        return Math.max(0, seconds * 1_000);
      }
    }
  } else if (!(error instanceof TypeError)) {
    // Fetch/stream network failures are TypeError; protocol/length errors are not.
    return null;
  }
  return 150 * 2 ** retry + Math.floor(Math.random() * 100);
};

const waitForRetry = (milliseconds: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });

const expectedContentRange = (range: ByteRange, total: number | null) => {
  const end = range.start + (range.length - 1);
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
    range.length < 1 ||
    !Number.isSafeInteger(range.start + (range.length - 1)) ||
    !Number.isSafeInteger(responseByteLength) ||
    responseByteLength < 1
  ) {
    throw new GsTileRangeError("Invalid GSTile byte range");
  }
  const end = range.start + (range.length - 1);
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
    void response.body?.cancel().catch(() => undefined);
    throw new GsTileHttpError(
      response.status, response.headers.get("retry-after"),
    );
  }
  if (!fullObjectResponse) {
    const contentRange = response.headers.get("content-range");
    const match = contentRange?.match(/^bytes (\d+)-(\d+)\/(\d+|\*)$/);
    if (
      !match ||
      Number(match[1]) !== range.start ||
      Number(match[2]) !== end ||
      (match[3] !== "*" &&
        (!Number.isSafeInteger(Number(match[3])) || Number(match[3]) <= end))
    ) {
      void response.body?.cancel().catch(() => undefined);
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
  activeNetwork: number;
  activePersistent: number;
  queuedNetwork: number;
  queuedPersistent: number;
  queued: number;
  queuedCritical: number;
  queuedPrefetch: number;
  priorityPromotions: number;
  prefetchCancellations: number;
  prefetchCompletedRequests: number;
  prefetchCompletedBytes: number;
  prefetchNetworkBytes: number;
  prefetchPersistentBytes: number;
  prefetchUsefulRequests: number;
  prefetchUsefulBytes: number;
  criticalQueueWaits: number;
  criticalQueueWaitMilliseconds: number;
  maximumCriticalQueueWaitMilliseconds: number;
  cacheEntries: number;
  cacheBytes: number;
  cacheProtectedBytes: number;
  prefetchCacheAdmissionRejections: number;
  cacheHits: number;
  cacheMisses: number;
  inFlightHits: number;
  persistentCacheHits: number;
  persistentCacheMisses: number;
  persistentWrites: number;
  persistentWriteBytes: number;
  persistentErrors: number;
  persistentAvailabilityQueries: number;
  persistentAvailabilityCandidates: number;
  persistentAvailabilityHits: number;
  networkBytes: number;
  networkRetries: number;
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
  originPriority: GsTileRangePriority;
  promote: (() => void) | null;
};

type QueueEntry = {
  resume: () => void;
  reject: (reason?: unknown) => void;
  signal?: AbortSignal;
  abort?: () => void;
  priority: GsTileRangePriority;
};

type RangeQueue = {
  maximumConcurrency: number;
  active: number;
  entries: QueueEntry[];
};

export class GsTileRangeScheduler {
  readonly #orphanGraceMilliseconds: number;
  readonly #persistentCache: GsTilePersistentCache | null;
  readonly #network: RangeQueue;
  readonly #persistent: RangeQueue;
  readonly #cache: GsTileMemoryRangeCache;
  #prefetchCacheAdmissionRejections = 0;
  #cacheHits = 0;
  #cacheMisses = 0;
  #inFlightHits = 0;
  #persistentCacheHits = 0;
  #persistentCacheMisses = 0;
  #persistentWrites = 0;
  #persistentWriteBytes = 0;
  #persistentErrors = 0;
  #persistentAvailabilityQueries = 0;
  #persistentAvailabilityCandidates = 0;
  #persistentAvailabilityHits = 0;
  #networkBytes = 0;
  #networkRetries = 0;
  #decodedBytes = 0;
  #zstdResponses = 0;
  #zstdFallbacks = 0;
  #priorityPromotions = 0;
  #prefetchCancellations = 0;
  #prefetchCompletedRequests = 0;
  #prefetchCompletedBytes = 0;
  #prefetchNetworkBytes = 0;
  #prefetchPersistentBytes = 0;
  #prefetchUsefulRequests = 0;
  #prefetchUsefulBytes = 0;
  #criticalQueueWaits = 0;
  #criticalQueueWaitMilliseconds = 0;
  #maximumCriticalQueueWaitMilliseconds = 0;
  #inFlight = new Map<string, InFlightRange>();
  #prefetchedMemory = new Set<string>();
  #persistentBuffers = new WeakSet<ArrayBuffer>();

  constructor(
    maximumConcurrency = 8,
    maximumCacheBytes = DEFAULT_GSTILE_MEMORY_CACHE_BYTES,
    orphanGraceMilliseconds = DEFAULT_GSTILE_ORPHAN_GRACE_MILLISECONDS,
    persistentCache: GsTilePersistentCache | null = null,
    maximumPersistentConcurrency = DEFAULT_GSTILE_PERSISTENT_READ_CONCURRENCY,
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
    if (!Number.isInteger(maximumPersistentConcurrency) || maximumPersistentConcurrency < 1) {
      throw new Error("GSTile persistent read concurrency must be a positive integer");
    }
    this.#network = { maximumConcurrency, active: 0, entries: [] };
    this.#persistent = {
      maximumConcurrency: maximumPersistentConcurrency, active: 0, entries: [],
    };
    this.#cache = new GsTileMemoryRangeCache(maximumCacheBytes, (key) => {
      this.#prefetchedMemory.delete(key);
    });
    this.#orphanGraceMilliseconds = orphanGraceMilliseconds;
    this.#persistentCache = persistentCache;
  }

  statistics(): GsTileRangeSchedulerStatistics {
    const queues = [...this.#network.entries, ...this.#persistent.entries];
    return {
      active: this.#network.active + this.#persistent.active,
      activeNetwork: this.#network.active,
      activePersistent: this.#persistent.active,
      queuedNetwork: this.#network.entries.length,
      queuedPersistent: this.#persistent.entries.length,
      queued: queues.length,
      queuedCritical: queues.filter(
        (entry) => entry.priority === "critical",
      ).length,
      queuedPrefetch: queues.filter(
        (entry) => entry.priority === "prefetch",
      ).length,
      priorityPromotions: this.#priorityPromotions,
      prefetchCancellations: this.#prefetchCancellations,
      prefetchCompletedRequests: this.#prefetchCompletedRequests,
      prefetchCompletedBytes: this.#prefetchCompletedBytes,
      prefetchNetworkBytes: this.#prefetchNetworkBytes,
      prefetchPersistentBytes: this.#prefetchPersistentBytes,
      prefetchUsefulRequests: this.#prefetchUsefulRequests,
      prefetchUsefulBytes: this.#prefetchUsefulBytes,
      criticalQueueWaits: this.#criticalQueueWaits,
      criticalQueueWaitMilliseconds: this.#criticalQueueWaitMilliseconds,
      maximumCriticalQueueWaitMilliseconds:
        this.#maximumCriticalQueueWaitMilliseconds,
      cacheEntries: this.#cache.size,
      cacheBytes: this.#cache.bytes,
      cacheProtectedBytes: this.#cache.protectedBytes,
      prefetchCacheAdmissionRejections: this.#prefetchCacheAdmissionRejections,
      cacheHits: this.#cacheHits,
      cacheMisses: this.#cacheMisses,
      inFlightHits: this.#inFlightHits,
      persistentCacheHits: this.#persistentCacheHits,
      persistentCacheMisses: this.#persistentCacheMisses,
      persistentWrites: this.#persistentWrites,
      persistentWriteBytes: this.#persistentWriteBytes,
      persistentErrors: this.#persistentErrors,
      persistentAvailabilityQueries: this.#persistentAvailabilityQueries,
      persistentAvailabilityCandidates:
        this.#persistentAvailabilityCandidates,
      persistentAvailabilityHits: this.#persistentAvailabilityHits,
      networkBytes: this.#networkBytes,
      networkRetries: this.#networkRetries,
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

  async locallyAvailable(
    requests: readonly GsTileRangeAvailabilityRequest[],
    signal?: AbortSignal,
  ) {
    signal?.throwIfAborted();
    const available = new Set<string>();
    const persistentRequests = new Map<
      string,
      { expectedByteLength: number; ids: string[] }
    >();
    for (const request of requests) {
      const key = this.#rangeKey(
        request.url,
        request.range,
        request.immutableIdentity,
      );
      if (this.#cache.has(key) || this.#inFlight.has(key)) {
        available.add(request.id);
      } else if (request.immutableIdentity && this.#persistentCache?.hasMany) {
        const candidate = persistentRequests.get(key);
        if (candidate) candidate.ids.push(request.id);
        else {
          persistentRequests.set(key, {
            expectedByteLength: request.range.length,
            ids: [request.id],
          });
        }
      }
    }
    if (!this.#persistentCache?.hasMany || persistentRequests.size === 0) {
      return available;
    }
    this.#persistentAvailabilityQueries += 1;
    this.#persistentAvailabilityCandidates += persistentRequests.size;
    try {
      const persistent = await this.#persistentCache.hasMany(
        [...persistentRequests].map(([key, entry]) => ({
          key,
          expectedByteLength: entry.expectedByteLength,
        })),
        signal,
      );
      signal?.throwIfAborted();
      this.#persistentAvailabilityHits += persistent.size;
      for (const key of persistent) {
        for (const id of persistentRequests.get(key)?.ids ?? []) {
          available.add(id);
        }
      }
    } catch (error) {
      if (signal?.aborted) throw error;
      this.#persistentErrors += 1;
    }
    return available;
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
    const cached = this.#cache.get(key, priority);
    if (cached) {
      this.#cacheHits += 1;
      if (priority === "critical" && this.#prefetchedMemory.delete(key)) {
        this.#prefetchUsefulRequests += 1;
        this.#prefetchUsefulBytes += cached.byteLength;
      }
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
      originPriority: priority,
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
    if (this.#persistentCache && key.startsWith("immutable:")) {
      await this.#acquire(this.#persistent, signal, request);
      try {
        signal.throwIfAborted();
        const persistent = await this.#persistentCache.read(key, range.length, signal);
        signal.throwIfAborted();
        if (persistent) {
          this.#persistentCacheHits += 1;
          this.#persistentBuffers.add(persistent);
          const admitted = this.#putMemory(key, persistent, request.priority);
          this.#recordPrefetchCompletion(key, request, persistent.byteLength, admitted, "persistent");
          return persistent;
        }
        this.#persistentCacheMisses += 1;
      } catch (error) {
        if (signal.aborted) throw error;
        this.#persistentErrors += 1;
      } finally {
        this.#release(this.#persistent);
      }
    }
    // A disk miss releases its read slot before waiting for network capacity.
    await this.#acquire(this.#network, signal, request);
    let slotHeld = true;
    const fetchWithRetry = async (...args: Parameters<typeof fetchGsTileRange>) => {
      for (let retry = 0; ; retry += 1) {
        try {
          signal.throwIfAborted();
          return await fetchGsTileRange(...args);
        } catch (error) {
          if (signal.aborted) throw error;
          // HTTP content-decoding failures also surface as TypeError. When a
          // raw fallback exists, try it immediately instead of repeating a
          // potentially unsupported browser decoding path.
          if (error instanceof TypeError && args[5] === false && transport?.fallbackUrl) {
            throw error;
          }
          const delay = retryDelay(error, retry);
          if (delay === null) throw error;
          this.#networkRetries += 1;
          // Sleeping prefetches must not occupy a slot needed by visible tiles.
          slotHeld = false;
          this.#release(this.#network);
          await waitForRetry(delay, signal);
          await this.#acquire(this.#network, signal, request);
          slotHeld = true;
        }
      }
    };
    try {
      const networkRange = transport
        ? { start: 0, length: transport.byteLength }
        : range;
      let encoded: ArrayBuffer;
      let content: ArrayBuffer;
      try {
        encoded = await fetchWithRetry(
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
        encoded = await fetchWithRetry(
          transport.fallbackUrl,
          range,
          signal,
          true,
        );
        content = encoded;
        transport = undefined;
      }
      signal.throwIfAborted();
      this.#networkBytes +=
        transport?.encoding === "zstd-http"
          ? transport.byteLength
          : encoded.byteLength;
      this.#decodedBytes += content.byteLength;
      if (transport?.encoding === "zstd" || transport?.encoding === "zstd-http") {
        this.#zstdResponses += 1;
      }
      const admitted = this.#putMemory(key, content, request.priority);
      this.#recordPrefetchCompletion(
        key,
        request,
        content.byteLength,
        admitted,
        "network",
        transport?.encoding === "zstd-http"
          ? transport.byteLength
          : encoded.byteLength,
      );
      return content;
    } finally {
      if (slotHeld) this.#release(this.#network);
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

  #recordPrefetchCompletion(
    key: string,
    request: InFlightRange,
    decodedByteLength: number,
    admitted: boolean,
    source: "network" | "persistent",
    networkByteLength = 0,
  ) {
    if (request.originPriority !== "prefetch") return;
    this.#prefetchCompletedRequests += 1;
    this.#prefetchCompletedBytes += decodedByteLength;
    if (source === "network") this.#prefetchNetworkBytes += networkByteLength;
    else this.#prefetchPersistentBytes += decodedByteLength;
    if (request.priority === "critical") {
      this.#prefetchUsefulRequests += 1;
      this.#prefetchUsefulBytes += decodedByteLength;
    } else if (admitted) {
      this.#prefetchedMemory.add(key);
    }
  }

  #putMemory(key: string, content: ArrayBuffer, priority: GsTileRangePriority) {
    const admitted = this.#cache.put(key, content, priority);
    if (!admitted && priority === "prefetch") this.#prefetchCacheAdmissionRejections += 1;
    return admitted;
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

  #enqueue(pool: RangeQueue, entry: QueueEntry) {
    if (entry.priority === "prefetch") {
      pool.entries.push(entry);
      return;
    }
    const firstPrefetch = pool.entries.findIndex(
      (candidate) => candidate.priority === "prefetch",
    );
    if (firstPrefetch < 0) pool.entries.push(entry);
    else pool.entries.splice(firstPrefetch, 0, entry);
  }

  #acquire(pool: RangeQueue, signal: AbortSignal | undefined, request: InFlightRange): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (pool.active < pool.maximumConcurrency) {
      pool.active += 1;
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
          pool.active += 1;
          resolve();
        },
        reject,
        signal,
        priority: request.priority,
      };
      queued.abort = () => {
        const index = pool.entries.indexOf(queued);
        if (index >= 0) pool.entries.splice(index, 1);
        request.promote = null;
        reject(signal?.reason);
      };
      request.promote = () => {
        const index = pool.entries.indexOf(queued);
        if (index < 0 || queued.priority === "critical") return;
        pool.entries.splice(index, 1);
        queued.priority = "critical";
        this.#enqueue(pool, queued);
      };
      signal?.addEventListener("abort", queued.abort, { once: true });
      this.#enqueue(pool, queued);
    });
  }

  #release(pool: RangeQueue) {
    pool.active -= 1;
    pool.entries.shift()?.resume();
  }
}
