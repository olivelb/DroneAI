/// <reference lib="webworker" />

import { decodeGsTileNativeTransformPayload } from "./native-transform-decode";
import type {
  GsTileTransformDecodeRequest,
  GsTileTransformDecodeResponse,
} from "./transform-decode-protocol";

const scope = self as DedicatedWorkerGlobalScope;

scope.onmessage = (event: MessageEvent<GsTileTransformDecodeRequest>) => {
  const request = event.data;
  try {
    const result = decodeGsTileNativeTransformPayload(
      request.payload,
      request.recordCount,
      request.quantization,
    );
    const response: GsTileTransformDecodeResponse = {
      type: "decoded",
      id: request.id,
      result,
    };
    scope.postMessage(response, [
      result.centerStream.buffer,
      result.transformA.buffer,
      result.transformB.buffer,
    ]);
  } catch (error) {
    const response: GsTileTransformDecodeResponse = {
      type: "error",
      id: request.id,
      message: error instanceof Error ? error.message : String(error),
    };
    scope.postMessage(response);
  }
};

export {};
