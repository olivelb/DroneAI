/// <reference lib="webworker" />

import { decodeGsTileNativePayload } from "./native-decode";
import type {
  GsTileDecodeWorkerRequest,
  GsTileDecodeWorkerResponse,
} from "./decode-worker-protocol";
import { GSTILE_RECORD_BYTES } from "./pack";

const scope = self as DedicatedWorkerGlobalScope;

scope.onmessage = (event: MessageEvent<GsTileDecodeWorkerRequest>) => {
  const request = event.data;
  try {
    const started = performance.now();
    const result = decodeGsTileNativePayload(
      new Uint8Array(request.payload, 0, request.recordCount * GSTILE_RECORD_BYTES),
      request.recordCount,
      request.quantization,
    );
    const response: GsTileDecodeWorkerResponse = {
      type: "decoded",
      id: request.id,
      result,
      computeMs: performance.now() - started,
      recycledPayload: request.recycleInput ? request.payload : undefined,
    };
    scope.postMessage(response, [
      result.centerStream.buffer,
      result.transformA.buffer,
      result.transformB.buffer,
      result.colorStream.buffer,
      ...result.shStreams.map((stream) => stream.buffer),
      ...result.opacityStreams.map((stream) => stream.buffer),
      ...(request.recycleInput ? [request.payload] : []),
    ]);
  } catch (error) {
    const response: GsTileDecodeWorkerResponse = {
      type: "error",
      id: request.id,
      message: error instanceof Error ? error.message : String(error),
    };
    scope.postMessage(response);
  }
};

export {};
