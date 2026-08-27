import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeDecodeResult } from "./native-decode";

export type GsTileDecodeWorkerRequest = {
  type: "decode";
  id: number;
  payload: ArrayBuffer;
  recordCount: number;
  quantization: GsTileQuantization;
  recycleInput: boolean;
};

export type GsTileDecodeWorkerResponse =
  | {
      type: "decoded";
      id: number;
      result: GsTileNativeDecodeResult;
      computeMs: number;
      recycledPayload?: ArrayBuffer;
    }
  | { type: "error"; id: number; message: string };
