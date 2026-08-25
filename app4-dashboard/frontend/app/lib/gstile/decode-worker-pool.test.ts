import { describe, expect, it } from "vitest";
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
  terminated = false;

  postMessage(message: GsTileDecodeWorkerRequest) {
    this.requests.push(message);
  }

  respond(index = 0) {
    const request = this.requests[index];
    this.onmessage?.({
      data: { type: "decoded", id: request.id, result: result(request.recordCount) },
    } as MessageEvent<GsTileDecodeWorkerResponse>);
  }

  terminate() {
    this.terminated = true;
  }
}

describe("GSTile decode Worker pool", () => {
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
