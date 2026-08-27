import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeDecodeResult } from "./native-decode";
import type { GsTileWorkerDecodeTiming } from "./decode-telemetry";
import { GSTILE_RECORD_BYTES } from "./pack";
import type {
  GsTileDecodeWorkerRequest,
  GsTileDecodeWorkerResponse,
} from "./decode-worker-protocol";

export type GsTileWorkerDecodeResult = {
  result: GsTileNativeDecodeResult;
  timing: GsTileWorkerDecodeTiming;
};

type DecodeTask = {
  id: number;
  content: ArrayBuffer;
  byteOffset: number;
  byteLength: number;
  recordCount: number;
  quantization: GsTileQuantization;
  signal: AbortSignal;
  resolve: (result: GsTileWorkerDecodeResult) => void;
  reject: (reason: unknown) => void;
  abort: () => void;
  settled: boolean;
  queuedAt: number;
  dispatchedAt: number;
  timing: GsTileWorkerDecodeTiming;
  recycleCapacity: number;
};

type WorkerSlot = {
  worker: GsTileDecodeWorker;
  task: DecodeTask | null;
  spare: ArrayBuffer | null;
};

export type GsTileDecodeWorker = {
  onmessage: ((event: MessageEvent<GsTileDecodeWorkerResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent) => void) | null;
  postMessage: (message: GsTileDecodeWorkerRequest, transfer: Transferable[]) => void;
  terminate: () => void;
};

export type GsTileDecodeWorkerFactory = () => GsTileDecodeWorker;

const defaultWorkerCount = () =>
  Math.min(4, Math.max(1, (globalThis.navigator?.hardwareConcurrency ?? 4) - 2));

// One spare per slot: <=32 MiB for the default four Workers, <=64 MiB for eight.
// Separate from the assembly's logical in-flight payload budget; never cache inputs.
export const GSTILE_DECODE_INPUT_BUFFER_CAP = 8 * 1024 * 1024;

const createDecodeWorker: GsTileDecodeWorkerFactory = () =>
  new Worker(new URL("./decode-worker.ts", import.meta.url), {
    type: "module",
    name: "gstile-decode",
  });

/** Bounded pool for decoding Q96 directly into final PlayCanvas streams. */
export class GsTileDecodeWorkerPool {
  readonly #slots: WorkerSlot[];
  readonly #queue: DecodeTask[] = [];
  readonly #workerFactory: GsTileDecodeWorkerFactory;
  readonly #recycleInput: boolean;
  #nextId = 1;
  #disposed = false;

  constructor(
    workerCount = defaultWorkerCount(),
    workerFactory = createDecodeWorker,
    recycleInput = true,
  ) {
    if (!Number.isSafeInteger(workerCount) || workerCount < 1 || workerCount > 8) {
      throw new Error("GSTile transform Worker count must be between 1 and 8");
    }
    this.#workerFactory = workerFactory;
    this.#recycleInput = recycleInput;
    this.#slots = [];
    try {
      for (let index = 0; index < workerCount; index += 1) {
        this.#slots.push(this.#createSlot());
      }
    } catch (error) {
      this.dispose(error);
      throw error;
    }
  }

  #createSlot(): WorkerSlot {
    const slot: WorkerSlot = {
      worker: this.#workerFactory(),
      task: null,
      spare: null,
    };
    slot.worker.onmessage = (
      event: MessageEvent<GsTileDecodeWorkerResponse>,
    ) => this.#complete(slot, event.data);
    slot.worker.onerror = (event) => {
      event.preventDefault();
      this.#failSlot(slot, new Error(event.message || "GSTile transform Worker failed"));
      this.#drain();
    };
    slot.worker.onmessageerror = () => {
      this.#failSlot(slot, new Error("GSTile transform Worker response is invalid"));
      this.#drain();
    };
    return slot;
  }

  get retainedInputBytes() {
    return this.#slots.reduce((bytes, slot) => bytes + (slot.spare?.byteLength ?? 0), 0);
  }

  decode(
    content: ArrayBuffer,
    byteOffset: number,
    byteLength: number,
    recordCount: number,
    quantization: GsTileQuantization,
    signal: AbortSignal,
  ) {
    if (this.#disposed) {
      return Promise.reject(new Error("GSTile transform Worker pool is disposed"));
    }
    if (
      !Number.isSafeInteger(recordCount) ||
      recordCount < 1 ||
      !Number.isSafeInteger(byteOffset) ||
      byteOffset < 0 ||
      !Number.isSafeInteger(byteLength) ||
      byteLength !== recordCount * GSTILE_RECORD_BYTES ||
      byteOffset + byteLength > content.byteLength
    ) {
      return Promise.reject(
        new Error("GSTile transform Worker range is inconsistent"),
      );
    }
    return new Promise<GsTileWorkerDecodeResult>((resolve, reject) => {
      const task: DecodeTask = {
        id: this.#nextId++,
        content,
        byteOffset,
        byteLength,
        recordCount,
        quantization,
        signal,
        resolve,
        reject,
        abort: () => undefined,
        settled: false,
        queuedAt: performance.now(),
        dispatchedAt: 0,
        timing: {
          queueMs: 0,
          inputCopyMs: 0,
          inputCopyBytes: 0,
          inputAllocatedBytes: 0,
          inputReusedBytes: 0,
          roundTripMs: 0,
          computeMs: 0,
        },
        recycleCapacity: 0,
      };
      const abort = () => {
        if (task.settled) return;
        task.settled = true;
        const queuedIndex = this.#queue.indexOf(task);
        if (queuedIndex >= 0) this.#queue.splice(queuedIndex, 1);
        reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      };
      task.abort = abort;
      if (signal.aborted) {
        abort();
        return;
      }
      signal.addEventListener("abort", abort, { once: true });
      this.#queue.push(task);
      this.#drain();
    });
  }

  #drain() {
    if (this.#disposed) return;
    for (let index = 0; index < this.#slots.length; index += 1) {
      const slot = this.#slots[index];
      if (slot.task) continue;
      let task = this.#queue.shift();
      while (task?.settled) task = this.#queue.shift();
      if (!task) return;
      slot.task = task;
      const copyStarted = performance.now();
      task.timing.queueMs = copyStarted - task.queuedAt;
      try {
        // The original belongs to the range cache and must not be detached.
        let payload = slot.spare;
        slot.spare = null;
        if (payload && payload.byteLength >= task.byteLength) {
          new Uint8Array(payload, 0, task.byteLength).set(
            new Uint8Array(task.content, task.byteOffset, task.byteLength),
          );
          task.timing.inputReusedBytes = task.byteLength;
        } else {
          payload = task.content.slice(task.byteOffset, task.byteOffset + task.byteLength);
          task.timing.inputAllocatedBytes = payload.byteLength;
        }
        task.timing.inputCopyMs = performance.now() - copyStarted;
        task.timing.inputCopyBytes = task.byteLength;
        task.recycleCapacity = this.#recycleInput && payload.byteLength <= GSTILE_DECODE_INPUT_BUFFER_CAP
          ? payload.byteLength : 0;
        const request: GsTileDecodeWorkerRequest = {
          type: "decode",
          id: task.id,
          payload,
          recordCount: task.recordCount,
          quantization: task.quantization,
          recycleInput: task.recycleCapacity > 0,
        };
        task.dispatchedAt = performance.now();
        slot.worker.postMessage(request, [payload]);
      } catch (error) {
        this.#failSlot(
          slot,
          error instanceof Error ? error : new Error(String(error)),
        );
        if (this.#disposed) return;
        // Retry queued work on the replacement, without recursive draining.
        index -= 1;
      }
    }
  }

  #complete(slot: WorkerSlot, response: GsTileDecodeWorkerResponse) {
    const completedAt = performance.now();
    const task = slot.task;
    if (!task || response.id !== task.id) {
      this.#failSlot(slot, new Error("GSTile transform Worker response is stale"));
      this.#drain();
      return;
    }
    if (
      response.type === "decoded" &&
      (!Number.isFinite(response.computeMs) || response.computeMs < 0)
    ) {
      this.#failSlot(slot, new Error("GSTile transform Worker timing is invalid"));
      this.#drain();
      return;
    }
    if (response.type === "decoded" && task.recycleCapacity) {
      const recycled = response.recycledPayload;
      const result = response.result;
      // A returned scratch buffer must not alias any published output or the cache.
      const validStreams = result && Array.isArray(result.shStreams) && Array.isArray(result.opacityStreams);
      const outputs = validStreams ? [result.centerStream, result.transformA, result.transformB,
        result.colorStream, ...result.shStreams, ...result.opacityStreams] : [];
      if (!(recycled instanceof ArrayBuffer) || recycled.byteLength !== task.recycleCapacity ||
          !validStreams || recycled === task.content || outputs.some(stream => stream?.buffer === recycled)) {
        this.#failSlot(slot, new Error("GSTile transform Worker recycled input is invalid"));
        this.#drain();
        return;
      }
      slot.spare = recycled;
    }
    slot.task = null;
    if (!task.settled) {
      task.settled = true;
      task.signal.removeEventListener("abort", task.abort);
      if (response.type === "decoded") {
        task.timing.roundTripMs = completedAt - task.dispatchedAt;
        task.timing.computeMs = response.computeMs;
        task.resolve({ result: response.result, timing: task.timing });
      } else {
        task.reject(new Error(response.message));
      }
    }
    this.#drain();
  }

  #failSlot(slot: WorkerSlot, error: Error) {
    const slotIndex = this.#slots.indexOf(slot);
    if (slotIndex < 0) return;
    const task = slot.task;
    slot.task = null;
    if (task && !task.settled) {
      task.settled = true;
      task.signal.removeEventListener("abort", task.abort);
      task.reject(error);
    }
    this.#terminate(slot);
    if (!this.#disposed) {
      try {
        this.#slots[slotIndex] = this.#createSlot();
      } catch (replacementError) {
        // No task may remain pending if the browser cannot create a Worker.
        this.dispose(replacementError);
      }
    }
  }

  #terminate(slot: WorkerSlot) {
    slot.spare = null;
    slot.worker.onmessage = null;
    slot.worker.onerror = null;
    slot.worker.onmessageerror = null;
    slot.worker.terminate();
  }

  dispose(reason: unknown = new DOMException("Disposed", "AbortError")) {
    if (this.#disposed) return;
    this.#disposed = true;
    for (const task of this.#queue.splice(0)) {
      if (!task.settled) {
        task.settled = true;
        task.signal.removeEventListener("abort", task.abort);
        task.reject(reason);
      }
    }
    for (const slot of this.#slots) {
      if (slot.task && !slot.task.settled) {
        slot.task.settled = true;
        slot.task.signal.removeEventListener("abort", slot.task.abort);
        slot.task.reject(reason);
      }
      this.#terminate(slot);
      slot.task = null;
    }
  }
}
