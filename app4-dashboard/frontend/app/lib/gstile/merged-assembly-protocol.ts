import type { GsTilePlayCanvasColumns } from "./decode";
import type { GsTileNativeDecodeResult } from "./native-decode";

export type GsTileAssemblyRequest =
  | { type: "init"; id: number; capacity: number; counts: number[]; textureWidth: number }
  | { type: "copy"; id: number; offset: number; result: GsTileNativeDecodeResult }
  | { type: "finish"; id: number };

export type GsTileAssemblyResponse =
  | { type: "ready"; id: number }
  | { type: "copied"; id: number; copyMs: number; bytes: number }
  | { type: "finished"; id: number; columns: GsTilePlayCanvasColumns }
  | { type: "error"; id: number; message: string };
