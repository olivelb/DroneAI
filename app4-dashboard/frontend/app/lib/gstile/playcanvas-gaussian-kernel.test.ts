import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { nativeGaussianRasterChunks } from "./playcanvas-gaussian-kernel";

const chunk = (name: string) => readFileSync(resolve(
  "node_modules/playcanvas/build/playcanvas/src/scene/shader-lib/wgsl/chunks/gsplat", name,
), "utf8");

describe("native-compatible Gaussian alpha", () => {
  it("uses the installed engine anchors and preserves premultiplied OVER output", () => {
    const result = nativeGaussianRasterChunks(chunk("frag/gsplat.js"), chunk("vert/gsplatHybrid.js"));
    expect(result.fragment).toContain("return exp(x * half(-4.0) / half(uniform.gstileEdgeOpacity));");
    expect(result.fragment).toContain("if (A > half(1.125))");
    expect(result.fragment).toContain("uniform gstileEdgeOpacity: f32;");
    expect(result.vertex).toContain("uniform gstileEdgeOpacity: f32;");
    expect(result.vertex).toContain("log(alpha / alphaClipValue) * half(uniform.gstileEdgeOpacity)");
    expect(result.vertex).toContain("min(half(1.0606601717798212),");
    expect(result.fragment).toContain("fragColor.xyz * fragColor.a, fragColor.a");
  });
  it("fails on engine drift instead of silently changing reconstruction opacity", () => {
    expect(() => nativeGaussianRasterChunks("", "")).toThrow("Unsupported PlayCanvas");
  });
});
