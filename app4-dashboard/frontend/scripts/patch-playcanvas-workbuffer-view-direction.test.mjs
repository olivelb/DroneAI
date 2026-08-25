import { describe, expect, it } from "vitest";
import {
  patchGsplatWorkBufferPassSource,
  patchGsplatWorkBufferShaderSource,
} from "./patch-playcanvas-workbuffer-view-direction.mjs";

const shaderFixture = (language) => {
  const wgsl = language === "wgsl";
  return [
    '#include "gsplatModifyVS"',
    "\tvar worldScale = vec3f(1.0);",
    wgsl
      ? "\t#if SH_BANDS > 0\n\t\tvar dir: vec3f;\n\t#endif"
      : "\t#if SH_BANDS > 0\n\t\tvec3 dir;\n\t#endif",
    wgsl
      ? "\t\t#if SH_BANDS > 0\n\t\t\tdir = normalize(quatRotateInv(uniform.model_rotation, worldCenter - uniform.uCameraPosition));\n\t\t#endif"
      : "\t\t#if SH_BANDS > 0\n\t\t\tdir = normalize(quatRotateInv(model_rotation, worldCenter - uCameraPosition));\n\t\t#endif",
    wgsl
      ? "\t\t#if SH_BANDS > 0\n\t\t\tdir = normalize(center.view * mat3x3f(center.modelView[0].xyz, center.modelView[1].xyz, center.modelView[2].xyz));\n\t\t#endif"
      : "\t\t#if SH_BANDS > 0\n\t\t\tdir = normalize(center.view * mat3(center.modelView));\n\t\t#endif",
    wgsl
      ? "\t#endif\n\tvar color = getColor();"
      : "\t#endif\n\tvec4 color = getColor();",
    "\t#if SH_BANDS > 0\n\t\tcolor = color;\n\t#endif",
  ].join("\n");
};

describe("PlayCanvas work-buffer view-direction patch", () => {
  for (const language of ["glsl", "wgsl"]) {
    it(`shares the already-normalized ${language} direction exactly once`, () => {
      const source = shaderFixture(language);
      const patched = patchGsplatWorkBufferShaderSource(source, language);
      expect(patched).toContain("DroneAI shared work-buffer view direction");
      expect(patched).toContain("getDroneWorkBufferViewDirection");
      expect(patched.match(/droneWorkBufferViewDirection = dir/g)).toHaveLength(1);
      expect(patched.match(/SH_BANDS > 0 \|\| defined/g)).toHaveLength(3);
      expect(patchGsplatWorkBufferShaderSource(patched, language)).toBe(patched);
      expect(() => patchGsplatWorkBufferShaderSource("drift", language))
        .toThrow("Unexpected PlayCanvas");
    });
  }

  it("assigns stable profiler names to full and color-only passes", () => {
    const source = "class Pass {\n\t\tthis.colorOnly = colorOnly;\n}";
    const patched = patchGsplatWorkBufferPassSource(source);
    expect(patched).toContain('"GSplatWorkBufferColor"');
    expect(patched).toContain('"GSplatWorkBufferFull"');
    expect(patchGsplatWorkBufferPassSource(patched)).toBe(patched);
    expect(() => patchGsplatWorkBufferPassSource("drift"))
      .toThrow("Unexpected PlayCanvas");
  });
});
