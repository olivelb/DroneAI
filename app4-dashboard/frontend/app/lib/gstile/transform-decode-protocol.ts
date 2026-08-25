import type { GsTileQuantization } from "./contracts";
import type { GsTileNativeTransformDecodeResult } from "./native-transform-decode";

export type GsTileTransformDecodeRequest = {
  type: "decode";
  id: number;
  payload: ArrayBuffer;
  recordCount: number;
  quantization: GsTileQuantization;
};

export type GsTileTransformDecodeResponse =
  | {
      type: "decoded";
      id: number;
      result: GsTileNativeTransformDecodeResult;
    }
  | { type: "error"; id: number; message: string };
