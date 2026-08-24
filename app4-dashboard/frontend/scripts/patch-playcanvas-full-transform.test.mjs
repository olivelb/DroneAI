import { describe, expect, it } from "vitest";

import {
  patchFullTransformReadSource,
  patchFullTransformStreamsSource,
  patchFullTransformWriteSource,
} from "./patch-playcanvas-full-transform.mjs";

const readFixture = `var<private> cachedTransformA: vec4u;
var<private> cachedTransformB: vec2u;
var<private> cachedColor: vec4f;
cachedTransformA = loadDataTransformA();
\tcachedTransformB = loadDataTransformB().xy;
\treturn vec3f(bitcast<f32>(cachedTransformA.r), bitcast<f32>(cachedTransformA.g), bitcast<f32>(cachedTransformA.b));
let rotXY = unpack2x16float(cachedTransformA.a);
\tlet rotZscaleX = unpack2x16float(cachedTransformB.x);
\tlet rotXYZ = vec3f(rotXY, rotZscaleX.x);
\treturn vec4f(rotXYZ, sqrt(max(0.0, 1.0 - dot(rotXYZ, rotXYZ)))).wxyz;
let rotZscaleX = unpack2x16float(cachedTransformB.x);
\tlet scaleYZ = unpack2x16float(cachedTransformB.y);
\treturn vec3f(rotZscaleX.y, scaleYZ);`;

const writeFixture = `writeDataTransformA(vec4u(bitcast<u32>(center.x), bitcast<u32>(center.y), bitcast<u32>(center.z), pack2x16float(rotation.xy)));
\t\twriteDataTransformB(vec4u(pack2x16float(vec2f(rotation.z, scale.x)), pack2x16float(scale.yz), 0u, 0u));`;

const streamsFixture = `{ name: "dataTransformA", format: PIXELFORMAT_RGBA32U },
\t\t\t\t{ name: "dataTransformB", format: PIXELFORMAT_RG32U }`;

describe("PlayCanvas full-transform work-buffer patch", () => {
  it("preserves all quaternion and scale components", () => {
    const read = patchFullTransformReadSource(readFixture);
    const write = patchFullTransformWriteSource(writeFixture);
    const streams = patchFullTransformStreamsSource(streamsFixture);

    expect(read).toMatch(/cachedTransformC: vec4u/);
    expect(read).toMatch(/bitcast<f32>\(cachedTransformC\.y\)/);
    expect(read).toMatch(/bitcast<f32>\(cachedTransformC\.x\)/);
    expect(write).toMatch(/writeDataTransformC/);
    expect(streams).toMatch(/dataTransformC/);
    expect(streams).not.toMatch(/PIXELFORMAT_RG32U/);
  });

  it("is idempotent", () => {
    const read = patchFullTransformReadSource(readFixture);
    const write = patchFullTransformWriteSource(writeFixture);
    const streams = patchFullTransformStreamsSource(streamsFixture);

    expect(patchFullTransformReadSource(read)).toBe(read);
    expect(patchFullTransformWriteSource(write)).toBe(write);
    expect(patchFullTransformStreamsSource(streams)).toBe(streams);
  });

  it("fails closed on an unknown PlayCanvas source", () => {
    expect(() => patchFullTransformReadSource("unknown source")).toThrow(
      /expected one source fragment/,
    );
  });
});
