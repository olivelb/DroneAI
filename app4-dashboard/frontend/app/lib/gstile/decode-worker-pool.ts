import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeDecodeResult } from "./native-decode";
import { GSTILE_RECORD_BYTES } from "./pack";
import type {
  GsTileDecodeWorkerRequest,
  GsTileDecodeWorkerResponse,
} from "./decode-worker-protocol";

type DecodeTask = {
  id: number;
  content: ArrayBuffer;
  byteOffset: number;
  byteLength: number;
  recordCount: number;
  quantization: GsTileQuantization;
  signal: AbortSignal;
  resolve: (result: GsTileNativeDecodeResult) => void;
  reject: (reason: unknown) => void;
  abort: () => void;
  settled: boolean;
};

type WorkerSlot = {
  worker: GsTileDecodeWorker;
  task: DecodeTask | null;
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
  #nextId = 1;
  #disposed = false;

  constructor(
    workerCount = defaultWorkerCount(),
    workerFactory = createDecodeWorker,
  ) {
    if (!Number.isSafeInteger(workerCount) || workerCount < 1 || workerCount > 8) {
      throw new Error("GSTile transform Worker count must be between 1 and 8");
    }
    this.#workerFactory = workerFactory;
    this.#slots = Array.from({ length: workerCount }, () => this.#createSlot());
  }

  #createSlot(): WorkerSlot {
    const slot = {
      worker: this.#workerFactory(),
      task: null,
    };
    slot.worker.onmessage = (
      event: MessageEvent<GsTileDecodeWorkerResponse>,
    ) => this.#complete(slot, event.data);
    slot.worker.onerror = (event) => {
      event.preventDefault();
      this.#failSlot(slot, new Error(event.message || "GSTile transform Worker failed"));
    };
    slot.worker.onmessageerror = () =>
      this.#failSlot(slot, new Error("GSTile transform Worker response is invalid"));
    return slot;
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
    return new Promise<GsTileNativeDecodeResult>((resolve, reject) => {
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
      };
      const abort = () => {
        if (task.settled) return;
        task.settled = true;
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
    for (const slot of this.#slots) {
      if (slot.task) continue;
      let task = this.#queue.shift();
      while (task?.settled) task = this.#queue.shift();
      if (!task) return;
      slot.task = task;
      const payload = task.content.slice(
        task.byteOffset,
        task.byteOffset + task.byteLength,
      );
      const request: GsTileDecodeWorkerRequest = {
        type: "decode",
        id: task.id,
        payload,
        recordCount: task.recordCount,
        quantization: task.quantization,
      };
      slot.worker.postMessage(request, [payload]);
    }
  }

  #complete(slot: WorkerSlot, response: GsTileDecodeWorkerResponse) {
    const task = slot.task;
    if (!task || response.id !== task.id) {
      this.#failSlot(slot, new Error("GSTile transform Worker response is stale"));
      return;
    }
    slot.task = null;
    if (!task.settled) {
      task.settled = true;
      task.signal.removeEventListener("abort", task.abort);
      if (response.type === "decoded") task.resolve(response.result);
      else task.reject(new Error(response.message));
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
    slot.worker.terminate();
    if (!this.#disposed) {
      const replacement = this.#createSlot();
      this.#slots[slotIndex] = replacement;
    }
    this.#drain();
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
      slot.worker.terminate();
      slot.task = null;
    }
  }
}
