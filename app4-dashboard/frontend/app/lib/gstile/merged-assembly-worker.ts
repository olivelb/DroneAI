/// <reference lib="webworker" />
import { GsTileMergedAssembler, gsTileNativeColumnBuffers } from "./merged-assembly";
import type { GsTileAssemblyRequest, GsTileAssemblyResponse } from "./merged-assembly-protocol";

const scope = self as DedicatedWorkerGlobalScope;
let assembler: GsTileMergedAssembler | null = null;
let initialized = false;
scope.onmessage = (event: MessageEvent<GsTileAssemblyRequest>) => {
  const request = event.data;
  try {
    let response: GsTileAssemblyResponse;
    let transfer: ArrayBuffer[] = [];
    if (request.type === "init") {
      if (initialized) throw new Error("GSTile assembly Worker already initialized");
      assembler = new GsTileMergedAssembler(request.capacity, request.counts);
      initialized = true;
      response = { type: "ready", id: request.id };
    } else {
      if (!assembler) throw new Error("GSTile assembly Worker is not active");
      if (request.type === "copy") {
        const started = performance.now();
        const bytes = assembler.copy(request.offset, request.result);
        response = { type: "copied", id: request.id, bytes, copyMs: performance.now() - started };
      } else if (request.type === "finish") {
        const columns = assembler.finish();
        assembler = null;
        transfer = gsTileNativeColumnBuffers(columns);
        response = { type: "finished", id: request.id, columns };
      } else throw new Error("GSTile assembly request is invalid");
    }
    scope.postMessage(response, transfer);
  } catch (error) {
    assembler = null;
    const response: GsTileAssemblyResponse = {
      type: "error", id: request.id, message: error instanceof Error ? error.message : String(error),
    };
    scope.postMessage(response);
  }
};
