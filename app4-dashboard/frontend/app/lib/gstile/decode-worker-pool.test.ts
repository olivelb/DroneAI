import { describe, expect, it, vi } from "vitest";
import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeDecodeResult } from "./native-decode";
import {
  GsTileDecodeWorkerPool,
  type GsTileDecodeWorker,
} from "./decode-worker-pool";
import type {
  GsTileDecodeWorkerRequest,
  GsTileDecodeWorkerResponse,
} from "./decode-worker-protocol";

const quantization: GsTileQuantization = {
  position: { min: [0, 0, 0], max: [1, 1, 1] },
  logScale: { min: [-1, -1, -1], max: [1, 1, 1] },
  rotation: { encoding: "snorm16x4" },
  opacityLogit: { min: -1, max: 1 },
  colorDcScale: [1, 1, 1],
  colorShScale: new Array(45).fill(1),
  opacityShScale: new Array(15).fill(1),
  sourceColorShDegree: 3,
  sourceOpacityShDegree: 3,
};

const result = (count: number): GsTileNativeDecodeResult => ({
  count,
  centerStream: new Float32Array(count * 3),
  transformA: new Uint32Array(count * 4),
  transformB: new Uint16Array(count * 4),
  colorStream: new Uint16Array(count * 4),
  shStreams: [
    new Uint32Array(count * 4),
    new Uint32Array(count * 4),
    new Uint32Array(count * 4),
    new Uint32Array(count * 4),
  ],
  opacityStreams: [
    new Float32Array(count * 4),
    new Float32Array(count * 4),
    new Float32Array(count * 4),
    new Float32Array(count * 4),
  ],
  bounds: {
    minimum: [0, 0, 0],
    maximum: [1, 1, 1],
    valid: true,
  },
});

class FakeWorker implements GsTileDecodeWorker {
  onmessage: ((event: MessageEvent<GsTileDecodeWorkerResponse>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent) => void) | null = null;
  readonly requests: GsTileDecodeWorkerRequest[] = [];
  readonly transfers: Transferable[][] = [];
  terminated = false;

  postMessage(message: GsTileDecodeWorkerRequest, transfer: Transferable[]) {
    this.requests.push(message);
    this.transfers.push(transfer);
  }

  respond(index = 0) {
    const request = this.requests[index];
    this.onmessage?.({
      data: { type: "decoded", id: request.id, result: result(request.recordCount), computeMs: 5 },
    } as MessageEvent<GsTileDecodeWorkerResponse>);
  }

  terminate() {
    this.terminated = true;
  }
}

describe("GSTile decode Worker pool", () => {
  it("recovers its slot after a synchronous dispatch failure", async () => {
    const firstWorker = new FakeWorker();
    const replacement = new FakeWorker();
    const factory = vi.fn().mockReturnValueOnce(firstWorker).mockReturnValue(replacement);
    vi.spyOn(firstWorker, "postMessage").mockImplementation(() => {
      throw new DOMException("cannot transfer", "DataCloneError");
    });
    const pool = new GsTileDecodeWorkerPool(1, factory);
    const decode = () => pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, new AbortController().signal);
    try {
      await expect(decode()).rejects.toMatchObject({ name: "DataCloneError" });
      expect(firstWorker.terminated).toBe(true);
      const next = decode();
      expect(replacement.requests).toHaveLength(1);
      replacement.respond();
      await next;
    } finally {
      pool.dispose();
    }
  });

  it("terminates already-created Workers when pool construction fails", () => {
    const first = new FakeWorker();
    const factory = vi.fn().mockReturnValueOnce(first).mockImplementation(() => {
      throw new Error("Worker creation failed");
    });
    expect(() => new GsTileDecodeWorkerPool(2, factory)).toThrow("Worker creation failed");
    expect(first.terminated).toBe(true);
  });

  it("rejects all pending work when a replacement Worker cannot be created", async () => {
    const workers = [new FakeWorker(), new FakeWorker()];
    const factory = vi.fn()
      .mockReturnValueOnce(workers[0])
      .mockReturnValueOnce(workers[1])
      .mockImplementation(() => { throw new Error("creation failed"); });
    const pool = new GsTileDecodeWorkerPool(2, factory);
    const decode = () => pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, new AbortController().signal);
    const pending = [decode(), decode(), decode()];
    const assertions = [
      expect(pending[0]).rejects.toThrow("runtime failure"),
      expect(pending[1]).rejects.toThrow("creation failed"),
      expect(pending[2]).rejects.toThrow("creation failed"),
    ];
    workers[0].onerror?.({ message: "runtime failure", preventDefault: vi.fn() } as unknown as ErrorEvent);
    await Promise.all(assertions);
    expect(workers.every((worker) => worker.terminated)).toBe(true);
    expect(workers.every((worker) => worker.onmessage === null)).toBe(true);
    await expect(decode()).rejects.toThrow("disposed");
    pool.dispose();
  });

  it("recovers from a queued buffer becoming detached before dispatch", async () => {
    const first = new FakeWorker();
    const replacement = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, vi.fn().mockReturnValueOnce(first).mockReturnValue(replacement));
    const decode = (content: ArrayBuffer) => pool.decode(content, 0, 96, 1, quantization, new AbortController().signal);
    try {
      const active = decode(new ArrayBuffer(96));
      const content = new ArrayBuffer(96);
      const detached = decode(content);
      const rejected = expect(detached).rejects.toBeInstanceOf(TypeError);
      const next = decode(new ArrayBuffer(96));
      structuredClone(content, { transfer: [content] });
      first.respond();
      await active;
      await rejected;
      expect(first.terminated).toBe(true);
      expect(replacement.requests).toHaveLength(1);
      replacement.respond();
      await next;
    } finally {
      pool.dispose();
    }
  });

  it("keeps an aborted active slot busy until its response, then admits queued work", async () => {
    const worker = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    const controller = new AbortController();
    try {
      const active = pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, controller.signal);
      const rejected = expect(active).rejects.toMatchObject({ name: "AbortError" });
      const next = pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, new AbortController().signal);
      controller.abort();
      await rejected;
      expect(worker.requests).toHaveLength(1);
      expect(worker.terminated).toBe(false);
      worker.respond();
      expect(worker.requests).toHaveLength(2);
      worker.respond(1);
      await next;
    } finally {
      pool.dispose();
    }
  });

  it.each(["stale", "messageerror", "invalid timing"])("recovers queued work after %s", async (failure) => {
    const first = new FakeWorker();
    const replacement = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, vi.fn().mockReturnValueOnce(first).mockReturnValue(replacement));
    const decode = () => pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, new AbortController().signal);
    try {
      const active = decode();
      const rejected = expect(active).rejects.toBeInstanceOf(Error);
      const next = decode();
      if (failure === "messageerror") {
        first.onmessageerror?.({} as MessageEvent);
      } else {
        first.onmessage?.({ data: {
          type: "decoded", id: failure === "stale" ? -1 : first.requests[0].id,
          result: result(1), computeMs: failure === "invalid timing" ? NaN : 5,
        } } as MessageEvent<GsTileDecodeWorkerResponse>);
      }
      await rejected;
      expect(first.terminated).toBe(true);
      replacement.respond();
      await next;
    } finally {
      pool.dispose();
    }
  });

  it("separates queue, input copy, round-trip and Worker-local compute durations", async () => {
    let now = 0;
    const clock = vi.spyOn(performance, "now").mockImplementation(() => now);
    const worker = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    try {
      const active = pool.decode(new ArrayBuffer(96), 0, 96, 1, quantization, new AbortController().signal);
      now = 10;
      const content = new ArrayBuffer(128);
      vi.spyOn(content, "slice").mockImplementation((start, end) => {
        now += 3;
        return ArrayBuffer.prototype.slice.call(content, start, end);
      });
      const next = pool.decode(content, 32, 96, 1, quantization, new AbortController().signal);
      now = 20;
      worker.respond();
      await active;
      now = 40;
      worker.respond(1);
      const decoded = await next;
      expect(decoded.result.count).toBe(1);
      expect(decoded.timing).toEqual({
        queueMs: 10, inputCopyMs: 3, inputCopyBytes: 96, roundTripMs: 17, computeMs: 5,
      });
      expect(content.byteLength).toBe(128);
    } finally {
      pool.dispose();
      clock.mockRestore();
    }
  });

  it("transfers only an isolated range copy and preserves cached pack bytes", async () => {
    const worker = new FakeWorker();
    const post = vi.spyOn(worker, "postMessage");
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    const content = Uint8Array.from({ length: 160 }, (_, index) => index).buffer;
    const expected = new Uint8Array(content.slice(32, 128));
    try {
      const pending = pool.decode(content, 32, 96, 1, quantization, new AbortController().signal);
      const [request, transfer] = post.mock.calls[0];
      expect(new Uint8Array(request.payload)).toEqual(expected);
      expect(transfer).toEqual([request.payload]);
      structuredClone(request, { transfer });
      expect(request.payload.byteLength).toBe(0);
      expect(content.byteLength).toBe(160);
      expect(new Uint8Array(content, 32, 96)).toEqual(expected);
      worker.respond();
      await pending;
    } finally {
      pool.dispose();
    }
  });

  it.each([0, -1, 1.5, NaN, Number.MAX_SAFE_INTEGER + 1])("rejects invalid record count %s before dispatch", async (count) => {
    const worker = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    try {
      await expect(pool.decode(new ArrayBuffer(96), 0, 96, count, quantization, new AbortController().signal)).rejects.toThrow("range is inconsistent");
      expect(worker.requests).toHaveLength(0);
    } finally {
      pool.dispose();
    }
  });

  it("admits only one payload copy per available Worker", async () => {
    const worker = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    const signal = new AbortController().signal;
    const first = pool.decode(new ArrayBuffer(128), 32, 96, 1, quantization, signal);
    const second = pool.decode(new ArrayBuffer(128), 32, 96, 1, quantization, signal);

    expect(worker.requests).toHaveLength(1);
    expect(worker.requests[0].payload.byteLength).toBe(96);
    worker.respond(0);
    await first;
    expect(worker.requests).toHaveLength(2);
    worker.respond(1);
    await second;
    pool.dispose();
  });

  it("rejects an aborted queued decode without dispatching it", async () => {
    const worker = new FakeWorker();
    const pool = new GsTileDecodeWorkerPool(1, () => worker);
    const active = pool.decode(
      new ArrayBuffer(96),
      0,
      96,
      1,
      quantization,
      new AbortController().signal,
    );
    const queuedController = new AbortController();
    const queued = pool.decode(
      new ArrayBuffer(96),
      0,
      96,
      1,
      quantization,
      queuedController.signal,
    );
    queuedController.abort(new DOMException("superseded", "AbortError"));

    await expect(queued).rejects.toMatchObject({ name: "AbortError" });
    worker.respond(0);
    await active;
    expect(worker.requests).toHaveLength(1);
    pool.dispose();
    expect(worker.terminated).toBe(true);
  });
});
