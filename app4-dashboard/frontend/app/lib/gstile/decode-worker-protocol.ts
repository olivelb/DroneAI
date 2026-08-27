import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeDecodeResult } from "./native-decode";

export type GsTileDecodeWorkerRequest = {
  type: "decode";
  id: number;
  payload: ArrayBuffer;
  recordCount: number;
  quantization: GsTileQuantization;
};

export type GsTileDecodeWorkerResponse =
  | {
      type: "decoded";
      id: number;
      result: GsTileNativeDecodeResult;
      computeMs: number;
    }
  | { type: "error"; id: number; message: string };
