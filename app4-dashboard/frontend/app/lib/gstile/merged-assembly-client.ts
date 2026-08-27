import type { GsTileNativeDecodeResult } from "./native-decode";
import { gsTileTextureDimensions } from "./native-streams";
import type { GsTileAssemblyRequest, GsTileAssemblyResponse } from "./merged-assembly-protocol";
import {
  GSTILE_ASSEMBLY_MAX_TASKS, GSTILE_ASSEMBLY_MAX_WORKING_BYTES,
  gsTileDecodeWorkingBytes, gsTileNativeColumnBuffers, gsTileNativeResultBuffers,
} from "./merged-assembly";

export class GsTileAssemblyError extends Error {
  constructor(message: string) { super(message); this.name = "GsTileAssemblyError"; }
}

/** Only retry a failed pre-commit assembly while its requested cut is current. */
export const shouldRetryGsTileAssembly = (error: unknown, signal: AbortSignal, isCurrent: boolean) =>
  error instanceof GsTileAssemblyError && !signal.aborted && isCurrent;

export type GsTileAssemblyWorker = {
  onmessage: ((event: MessageEvent<GsTileAssemblyResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent) => void) | null;
  postMessage: (request: GsTileAssemblyRequest, transfer: Transferable[]) => void;
  terminate: () => void;
};
type Pending = {
  request: GsTileAssemblyRequest;
  resolve: (response: GsTileAssemblyResponse) => void;
  reject: (reason: unknown) => void;
  timer: ReturnType<typeof setTimeout>;
};
type Waiter = { bytes: number; resolve: (release: () => void) => void; reject: (reason: unknown) => void };

/** Dedicated cut owner. A permit covers decode, transfer AND copy acknowledgement. */
export class GsTileMergedAssemblyClient {
  readonly ready: Promise<void>;
  readonly #worker: GsTileAssemblyWorker;
  readonly #signal: AbortSignal;
  readonly #capacity: number;
  readonly #textureWidth: number;
  readonly #pending = new Map<number, Pending>();
  readonly #waiters: Waiter[] = [];
  #nextId = 1;
  #closed: unknown = null;
  #active = 0;
  #workingBytes = 0;
  #peakTasks = 0;
  #peakBytes = 0;
  #transferMs = 0;
  #finishing = false;

  constructor(
    capacity: number,
    counts: readonly number[],
    signal: AbortSignal,
    workerFactory: () => GsTileAssemblyWorker = () => new Worker(
      new URL("./merged-assembly-worker.ts", import.meta.url),
      { type: "module", name: "gstile-merged-assembly" },
    ),
    textureWidth?: number,
  ) {
    signal.throwIfAborted();
    this.#capacity = capacity;
    this.#textureWidth = gsTileTextureDimensions(capacity, textureWidth).width;
    this.#signal = signal;
    try { this.#worker = workerFactory(); }
    catch (error) { throw new GsTileAssemblyError(String(error)); }
    this.#worker.onmessage = event => this.#complete(event.data);
    this.#worker.onerror = event => {
      event.preventDefault();
      this.dispose(new GsTileAssemblyError(event.message || "GSTile assembly Worker failed"));
    };
    this.#worker.onmessageerror = () => this.dispose(new GsTileAssemblyError("GSTile assembly response is invalid"));
    signal.addEventListener("abort", this.#abort, { once: true });
    this.ready = this.#request({ type: "init", id: this.#nextId++, capacity, counts: [...counts], textureWidth: this.#textureWidth }).then(() => undefined);
  }

  #abort = () => this.dispose(this.#signal.reason ?? new DOMException("Aborted", "AbortError"));

  get statistics() {
    return { transferMs: this.#transferMs, peakTasks: this.#peakTasks, peakBytes: this.#peakBytes };
  }

  acquire(recordCount: number): Promise<() => void> {
    if (this.#closed) return Promise.reject(this.#closed);
    if (this.#finishing) return Promise.reject(new GsTileAssemblyError("GSTile assembly is finishing"));
    let bytes: number;
    try { bytes = gsTileDecodeWorkingBytes(recordCount); }
    catch (error) { return Promise.reject(error); }
    if (bytes > GSTILE_ASSEMBLY_MAX_WORKING_BYTES) {
      return Promise.reject(new GsTileAssemblyError("GSTile decode exceeds assembly admission budget"));
    }
    return new Promise((resolve, reject) => {
      this.#waiters.push({ bytes, resolve, reject });
      this.#drain();
    });
  }

  #drain() {
    while (!this.#closed && this.#active < GSTILE_ASSEMBLY_MAX_TASKS && this.#waiters.length) {
      const waiter = this.#waiters[0];
      if (this.#workingBytes + waiter.bytes > GSTILE_ASSEMBLY_MAX_WORKING_BYTES) return;
      this.#waiters.shift();
      this.#active++;
      this.#workingBytes += waiter.bytes;
      this.#peakTasks = Math.max(this.#peakTasks, this.#active);
      this.#peakBytes = Math.max(this.#peakBytes, this.#workingBytes);
      let released = false;
      waiter.resolve(() => {
        if (released) return;
        released = true;
        this.#active--;
        this.#workingBytes -= waiter.bytes;
        this.#drain();
      });
    }
  }

  async copy(offset: number, result: GsTileNativeDecodeResult) {
    if (!this.#active || this.#finishing) throw new GsTileAssemblyError("GSTile copy requires an active decode permit");
    const response = await this.#request(
      { type: "copy", id: this.#nextId++, offset, result }, gsTileNativeResultBuffers(result),
    );
    if (response.type !== "copied") throw new GsTileAssemblyError("GSTile copy response is inconsistent");
    return response;
  }

  async finish() {
    if (this.#active || this.#waiters.length || this.#finishing) {
      throw new GsTileAssemblyError("GSTile assembly still has active work");
    }
    this.#finishing = true;
    const response = await this.#request({ type: "finish", id: this.#nextId++ });
    if (response.type !== "finished") throw new GsTileAssemblyError("GSTile assembly result is inconsistent");
    this.dispose();
    return response.columns;
  }

  #request(request: GsTileAssemblyRequest, transfer: ArrayBuffer[] = []) {
    if (this.#closed) return Promise.reject<GsTileAssemblyResponse>(this.#closed);
    return new Promise<GsTileAssemblyResponse>((resolve, reject) => {
      const timer = setTimeout(() => this.dispose(new GsTileAssemblyError("GSTile assembly Worker timed out")), 30_000);
      this.#pending.set(request.id, { request, resolve, reject, timer });
      try {
        const started = performance.now();
        this.#worker.postMessage(request, transfer);
        this.#transferMs += performance.now() - started;
      } catch (error) { this.dispose(new GsTileAssemblyError(String(error))); }
    });
  }

  #complete(response: GsTileAssemblyResponse) {
    const pending = this.#pending.get(response.id);
    if (!pending) { this.dispose(new GsTileAssemblyError("GSTile assembly response is stale")); return; }
    if (response.type === "error") { this.dispose(new GsTileAssemblyError(response.message)); return; }
    const request = pending.request;
    try {
      if (request.type === "init" && response.type !== "ready" ||
          request.type === "copy" && (response.type !== "copied" ||
            !Number.isFinite(response.copyMs) || response.copyMs < 0 || response.bytes !== request.result.count * 172) ||
          request.type === "finish" && (response.type !== "finished" || response.columns.count !== this.#capacity ||
            response.columns.textureWidth !== this.#textureWidth)) {
        throw new Error("GSTile assembly response does not match its request");
      }
      if (response.type === "finished") gsTileNativeColumnBuffers(response.columns);
    } catch (error) { this.dispose(new GsTileAssemblyError(String(error))); return; }
    this.#pending.delete(response.id);
    clearTimeout(pending.timer);
    pending.resolve(response);
  }

  dispose(reason: unknown = new DOMException("Disposed", "AbortError")) {
    if (this.#closed) return;
    this.#closed = reason;
    this.#signal.removeEventListener("abort", this.#abort);
    this.#worker.onmessage = null;
    this.#worker.onerror = null;
    this.#worker.onmessageerror = null;
    this.#worker.terminate();
    for (const pending of this.#pending.values()) { clearTimeout(pending.timer); pending.reject(reason); }
    this.#pending.clear();
    for (const waiter of this.#waiters.splice(0)) waiter.reject(reason);
  }
}
