import { describe, expect, it } from "vitest";
import {
  adoptGsTileNativeRgba32Streams,
  gsTileTextureElementCapacity,
} from "./native-streams";

type TestTexture = {
  data?: Uint32Array;
  destroyed: boolean;
  destroy: () => void;
};

const texture = (data?: Uint32Array): TestTexture => {
  const result: TestTexture = {
    data,
    destroyed: false,
    destroy: () => {
      result.destroyed = true;
    },
  };
  return result;
};

describe("GSTile native stream adoption", () => {
  it("matches PlayCanvas texture capacity including padding", () => {
    expect(gsTileTextureElementCapacity(1)).toBe(1);
    expect(gsTileTextureElementCapacity(2)).toBe(2);
    expect(gsTileTextureElementCapacity(3)).toBe(4);
    expect(gsTileTextureElementCapacity(65_536)).toBe(65_536);
    expect(() => gsTileTextureElementCapacity(0)).toThrow(/must be positive/);
  });

  it("adopts prepacked sources and destroys replaced textures atomically", () => {
    const names = ["a", "b"] as const;
    const originals = names.map(() => texture());
    const sources = names.map(() => new Uint32Array(24));
    const textures = new Map<string, TestTexture>(
      names.map((name, index) => [name, originals[index]]),
    );
    const streams = {
      textureDimensions: { x: 3, y: 2 },
      textures,
      getTexture: (name: string) => textures.get(name),
      createTexture: (
        _name: string,
        _format: number,
        _size: { x: number; y: number },
        data: Uint32Array,
      ) => texture(data),
    };
    const format = {
      getStream: (name: string) =>
        names.includes(name as (typeof names)[number])
          ? { format: 17 }
          : undefined,
    };

    adoptGsTileNativeRgba32Streams(streams, format, names, sources);

    names.forEach((name, index) => {
      expect(textures.get(name)?.data).toBe(sources[index]);
      expect(originals[index].destroyed).toBe(true);
    });
  });

  it("keeps the original map and releases partial replacements on failure", () => {
    const names = ["a", "b"] as const;
    const originals = names.map(() => texture());
    const textures = new Map<string, TestTexture>(
      names.map((name, index) => [name, originals[index]]),
    );
    const created: TestTexture[] = [];
    const streams = {
      textureDimensions: { x: 2, y: 2 },
      textures,
      getTexture: (name: string) => textures.get(name),
      createTexture: (name: string) => {
        if (name === "b") throw new Error("allocation failed");
        const replacement = texture();
        created.push(replacement);
        return replacement;
      },
    };
    const format = { getStream: () => ({ format: 17 }) };
    const sources = names.map(() => new Uint32Array(16));

    expect(() =>
      adoptGsTileNativeRgba32Streams(streams, format, names, sources),
    ).toThrow(/allocation failed/);
    names.forEach((name, index) => {
      expect(textures.get(name)).toBe(originals[index]);
      expect(originals[index].destroyed).toBe(false);
    });
    expect(created[0].destroyed).toBe(true);
  });
});
