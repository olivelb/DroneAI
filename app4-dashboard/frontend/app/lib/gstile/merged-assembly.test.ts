import { describe, expect, it, vi } from "vitest";
import { allocateGsTilePlayCanvasColumns } from "./decode";
import { copyGsTileNativeResult, type GsTileNativeDecodeResult } from "./native-decode";
import {
  canAssembleGsTileInWorker, GsTileMergedAssembler, gsTileDecodeWorkingBytes,
  gsTileNativeColumnBuffers, gsTileNativeResultBuffers, GSTILE_ASSEMBLY_MAX_WORKING_BYTES,
} from "./merged-assembly";
import { GsTileMergedAssemblyClient, GsTileAssemblyError, shouldRetryGsTileAssembly, type GsTileAssemblyWorker } from "./merged-assembly-client";
import type { GsTileAssemblyRequest, GsTileAssemblyResponse } from "./merged-assembly-protocol";

const nativeResult = (count: number): GsTileNativeDecodeResult => {
  const columns = allocateGsTilePlayCanvasColumns(count, { color: true, centerBounds: true, sh: true, transform: true });
  gsTileNativeColumnBuffers(columns).forEach((buffer, index) => {
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i++) bytes[i] = (i * 37 + index * 19) % 256;
  });
  return { count, centerStream: columns.centerStream!, transformA: columns.transformStreams![0],
    transformB: columns.transformStreams![1], colorStream: columns.colorStream!,
    shStreams: columns.shStreams!, opacityStreams: columns.opacityStreams, bounds: columns.bounds };
};

class FakeWorker implements GsTileAssemblyWorker {
  onmessage: GsTileAssemblyWorker["onmessage"] = null;
  onerror: GsTileAssemblyWorker["onerror"] = null;
  onmessageerror: GsTileAssemblyWorker["onmessageerror"] = null;
  messages: GsTileAssemblyRequest[] = [];
  terminate = vi.fn();
  postMessage = vi.fn((request: GsTileAssemblyRequest, transfer: Transferable[]) => {
    this.messages.push(structuredClone(request, { transfer }));
  });
  respond(response: GsTileAssemblyResponse) { this.onmessage?.({ data: response } as MessageEvent<GsTileAssemblyResponse>); }
}
const readyClient = async () => {
  const worker = new FakeWorker(), controller = new AbortController();
  const client = new GsTileMergedAssemblyClient(20, [2, 3], controller.signal, () => worker);
  worker.respond({ type: "ready", id: 1 }); await client.ready;
  return { client, worker, controller };
};

describe("GSTile owned merged assembly", () => {
  it("matches all main-thread output bits and padding with out-of-order results", () => {
    const sources = [nativeResult(2), nativeResult(3), nativeResult(5)];
    const expected = allocateGsTilePlayCanvasColumns(17, { color: true, centerBounds: true, sh: true, transform: true });
    const assembly = new GsTileMergedAssembler(17, [2, 3, 5]);
    const offsets = [0, 2, 5];
    for (const i of [2, 0, 1]) {
      expect(assembly.copy(offsets[i], sources[i])).toBe(sources[i].count * 172);
      copyGsTileNativeResult(expected, offsets[i], sources[i]);
    }
    const actual = gsTileNativeColumnBuffers(assembly.finish());
    gsTileNativeColumnBuffers(expected).forEach((buffer, i) => expect(new Uint8Array(actual[i])).toEqual(new Uint8Array(buffer)));
    expect(() => assembly.finish()).toThrow("already finished");
  });

  it("rejects missing, duplicate, unexpected and malformed ranges", () => {
    const assembly = new GsTileMergedAssembler(5, [2, 3]);
    expect(() => assembly.finish()).toThrow("incomplete");
    expect(() => assembly.copy(1, nativeResult(2))).toThrow("unexpected");
    const malformed = nativeResult(2); malformed.transformA = new Uint32Array(0);
    expect(() => assembly.copy(0, malformed)).toThrow("inconsistent");
    assembly.copy(0, nativeResult(2));
    expect(() => assembly.copy(0, nativeResult(2))).toThrow("already written");
    assembly.copy(2, nativeResult(3));
    expect(assembly.finish().count).toBe(5);
  });

  it("does not transfer larger backing buffers or aliased buffers", () => {
    const result = nativeResult(2);
    result.centerStream = new Float32Array(new ArrayBuffer(100), 4, 6);
    expect(() => gsTileNativeResultBuffers(result)).toThrow("backing");
    const alias = nativeResult(2);
    alias.shStreams = [alias.shStreams[0], alias.shStreams[0], alias.shStreams[2], alias.shStreams[3]];
    expect(() => gsTileNativeResultBuffers(alias)).toThrow("twelve owned");
  });

  it.each([[0, [1]], [1, []], [1, [2]], [3, [0, 3]], [3, [1.5]], [20_000_000, [1]]])(
    "rejects an invalid capacity/plan before allocating (%s)", (capacity, counts) => {
      expect(() => new GsTileMergedAssembler(capacity as number, counts as number[])).toThrow();
    },
  );

  it("offloads only sufficiently large cuts with bounded tile and destination sizes", () => {
    expect(canAssembleGsTileInWorker(3_000_000, Array(48).fill(65_536))).toBe(false);
    expect(canAssembleGsTileInWorker(3_000_000, Array(40).fill(65_536))).toBe(true);
    expect(canAssembleGsTileInWorker(3_000_000, Array(10).fill(65_536))).toBe(false);
    expect(canAssembleGsTileInWorker(3_000_000, [3_000_000])).toBe(false);
    expect(canAssembleGsTileInWorker(20_000_000, Array(40).fill(65_536))).toBe(false);
    expect(canAssembleGsTileInWorker(3_000_000, [-1])).toBe(false);
  });
});

describe("GSTile assembly Worker ownership and admission", () => {
  it("retries only assembly failures for a still-current, live request", () => {
    const controller = new AbortController(), error = new GsTileAssemblyError("Worker failed");
    expect(shouldRetryGsTileAssembly(error, controller.signal, true)).toBe(true);
    expect(shouldRetryGsTileAssembly(error, controller.signal, false)).toBe(false);
    expect(shouldRetryGsTileAssembly(new Error("GPU failed"), controller.signal, true)).toBe(false);
    controller.abort();
    expect(shouldRetryGsTileAssembly(error, controller.signal, true)).toBe(false);
  });
  it("bounds decoder-to-copy lifetime to four permits and releases once", async () => {
    const { client } = await readyClient();
    const releases = await Promise.all(Array.from({ length: 4 }, () => client.acquire(65_536)));
    let fifthReady = false;
    const fifth = client.acquire(65_536).then(release => { fifthReady = true; return release; });
    await Promise.resolve(); expect(fifthReady).toBe(false);
    releases[0](); releases[0]();
    const releaseFifth = await fifth;
    expect(client.statistics.peakTasks).toBe(4);
    expect(client.statistics.peakBytes).toBe(4 * gsTileDecodeWorkingBytes(65_536));
    releases.slice(1).forEach(release => release()); releaseFifth(); client.dispose();
  });

  it("enforces the byte cap before decoding, not only the task count", async () => {
    const { client } = await readyClient();
    const release = await client.acquire(300_000);
    let admitted = false;
    const second = client.acquire(300_000).then(r => { admitted = true; return r; });
    await Promise.resolve(); expect(admitted).toBe(false);
    release(); (await second)();
    expect(client.statistics.peakTasks).toBe(1);
    expect(client.statistics.peakBytes).toBeLessThanOrEqual(GSTILE_ASSEMBLY_MAX_WORKING_BYTES);
    await expect(client.acquire(1_000_000)).rejects.toThrow("budget");
    client.dispose();
  });

  it("transfers source ownership and waits for acknowledgement before completion", async () => {
    const { client, worker } = await readyClient();
    const release = await client.acquire(2), source = nativeResult(2);
    const buffers = gsTileNativeResultBuffers(source);
    let copied = false;
    const copy = client.copy(0, source).then(result => { copied = true; return result; });
    expect(buffers.every(buffer => buffer.byteLength === 0)).toBe(true);
    await Promise.resolve(); expect(copied).toBe(false);
    await expect(client.finish()).rejects.toThrow("active work");
    worker.respond({ type: "copied", id: 2, copyMs: 3, bytes: 344 });
    expect((await copy).copyMs).toBe(3); release();
    const finished = client.finish();
    const columns = allocateGsTilePlayCanvasColumns(20, { color: true, centerBounds: true, sh: true, transform: true });
    worker.respond({ type: "finished", id: 3, columns });
    expect(await finished).toBe(columns);
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it.each(["abort", "error", "messageerror", "post", "stale", "wrong-type", "bad-timing", "wrong-bytes"])(
    "settles active copies and queued admission on %s", async kind => {
      const { client, worker, controller } = await readyClient();
      const releases = await Promise.all(Array.from({ length: 4 }, () => client.acquire(2)));
      const queued = client.acquire(2), queuedCheck = expect(queued).rejects.toBeDefined();
      if (kind === "post") worker.postMessage.mockImplementation(() => { throw new Error("post failed"); });
      const copy = client.copy(0, nativeResult(2)), copyCheck = expect(copy).rejects.toBeDefined();
      if (kind === "abort") controller.abort(new DOMException("Superseded", "AbortError"));
      if (kind === "error") worker.onerror?.({ message: "crash", preventDefault: () => undefined } as ErrorEvent);
      if (kind === "messageerror") worker.onmessageerror?.({} as MessageEvent);
      if (kind === "stale") worker.respond({ type: "copied", id: 99, copyMs: 1, bytes: 344 });
      if (kind === "wrong-type") worker.respond({ type: "ready", id: 2 });
      if (kind === "bad-timing") worker.respond({ type: "copied", id: 2, copyMs: NaN, bytes: 344 });
      if (kind === "wrong-bytes") worker.respond({ type: "copied", id: 2, copyMs: 1, bytes: 172 });
      await Promise.all([copyCheck, queuedCheck]);
      releases.forEach(release => release());
      expect(worker.terminate).toHaveBeenCalledOnce();
      expect(worker.onmessage).toBeNull();
      await expect(client.acquire(2)).rejects.toBeDefined();
    },
  );

  it("times out a silent Worker and rejects initialization", async () => {
    vi.useFakeTimers();
    try {
      const worker = new FakeWorker();
      const client = new GsTileMergedAssemblyClient(2, [2], new AbortController().signal, () => worker);
      const checked = expect(client.ready).rejects.toBeInstanceOf(GsTileAssemblyError);
      await vi.advanceTimersByTimeAsync(30_000); await checked;
      expect(worker.terminate).toHaveBeenCalledOnce();
    } finally { vi.useRealTimers(); }
  });

  it("wraps Worker construction failures and avoids construction on pre-abort", () => {
    const factory = vi.fn(() => { throw new Error("unavailable"); });
    expect(() => new GsTileMergedAssemblyClient(2, [2], new AbortController().signal, factory)).toThrow(GsTileAssemblyError);
    const controller = new AbortController(); controller.abort();
    expect(() => new GsTileMergedAssemblyClient(2, [2], controller.signal, factory)).toThrow();
    expect(factory).toHaveBeenCalledOnce();
  });
});
