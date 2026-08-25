/// <reference lib="webworker" />

import { decodeGsTileNativePayload } from "./native-decode";
import type {
  GsTileDecodeWorkerRequest,
  GsTileDecodeWorkerResponse,
} from "./decode-worker-protocol";

const scope = self as DedicatedWorkerGlobalScope;

scope.onmessage = (event: MessageEvent<GsTileDecodeWorkerRequest>) => {
  const request = event.data;
  try {
    const result = decodeGsTileNativePayload(
      request.payload,
      request.recordCount,
      request.quantization,
    );
    const response: GsTileDecodeWorkerResponse = {
      type: "decoded",
      id: request.id,
      result,
    };
    scope.postMessage(response, [
      result.centerStream.buffer,
      result.transformA.buffer,
      result.transformB.buffer,
      result.colorStream.buffer,
      ...result.shStreams.map((stream) => stream.buffer),
      ...result.opacityStreams.map((stream) => stream.buffer),
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
