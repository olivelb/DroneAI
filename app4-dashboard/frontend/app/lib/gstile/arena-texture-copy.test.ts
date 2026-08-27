import { afterEach, describe, expect, it, vi } from "vitest";
import * as arena from "./merged-arena";
import { copyGsTileTextureRange } from "./arena-texture-copy";

afterEach(() => vi.restoreAllMocks());

function fixture(shapes = Array.from({ length: 11 }, () => [4, 3, 3, 4])) {
  const sources = shapes.map(([width, height]) => ({ width, height }));
  const destinations = shapes.map(([, , width, height]) => ({
    width, height, copy: vi.fn(() => true),
  }));
  return {
    sources, destinations,
    source: {
      format: { resourceStreams: shapes.map((_, i) => ({ name: String(i) })) },
      getTexture: (name: string) => sources[Number(name)],
    },
    destination: { getTexture: (name: string) => destinations[Number(name)] },
  };
}

describe("GSTile shared stream copy geometry", () => {
  it("plans once for eleven equal shapes and emits identical ordered copies", () => {
    const f = fixture();
    const expected = arena.planLinearTextureCopies(4, 3, 3, 3, 4, 2, 7);
    const spy = vi.spyOn(arena, "planLinearTextureCopies");
    copyGsTileTextureRange(f.source, f.destination, 3, 2, 7);
    expect(spy).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 11; i++) {
      expect(f.destinations[i].copy.mock.calls).toEqual(
        expected.map((options) => [f.sources[i], options]),
      );
    }
  });

  it.each([0, 1, 2, 3])("replans when dimension %i differs", (axis) => {
    const first = [4, 4, 4, 4];
    const second = [...first];
    second[axis] = 5;
    const f = fixture([first, second, second]);
    const spy = vi.spyOn(arena, "planLinearTextureCopies");
    copyGsTileTextureRange(f.source, f.destination, 3, 2, 7);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(f.destinations[1].copy.mock.calls).toEqual(
      arena.planLinearTextureCopies(second[0],second[1],3,second[2],second[3],2,7)
        .map(options => [f.sources[1], options]),
    );
  });

  it("never reuses a plan across range calls or retains more than the last shape", () => {
    const f = fixture([[4,4,4,4], [5,4,4,4], [4,4,4,4]]);
    const spy = vi.spyOn(arena, "planLinearTextureCopies");
    copyGsTileTextureRange(f.source, f.destination, 3, 2, 7);
    copyGsTileTextureRange(f.source, f.destination, 1, 4, 2);
    expect(spy).toHaveBeenCalledTimes(6);
    expect(spy).toHaveBeenLastCalledWith(4,4,1,4,4,4,2);
  });

  it("still rejects a smaller stream instead of reusing an out-of-bounds plan", () => {
    const f = fixture([[4,4,4,4], [4,1,4,4], [4,4,4,4]]);
    expect(() => copyGsTileTextureRange(f.source, f.destination, 3, 2, 7)).toThrow("escapes");
    expect(f.destinations[1].copy).not.toHaveBeenCalled();
    expect(f.destinations[2].copy).not.toHaveBeenCalled();
  });

  it("reports a missing stream by name", () => {
    const f = fixture();
    expect(() => copyGsTileTextureRange(
      { ...f.source, getTexture: () => undefined }, f.destination, 3, 2, 7,
    )).toThrow("stream 0 is unavailable");
    expect(f.destinations[0].copy).not.toHaveBeenCalled();
  });

  it("rejects a missing destination before attempting its copies", () => {
    const f = fixture();
    expect(() => copyGsTileTextureRange(
      f.source, { getTexture: () => null }, 3, 2, 7,
    )).toThrow("stream 0 is unavailable");
    expect(f.destinations[0].copy).not.toHaveBeenCalled();
  });

  it("preserves the full stream/rectangle sequence for thirteen diagnostic streams", () => {
    const f = fixture(Array.from({ length: 13 }, () => [7, 9, 8, 8]));
    const trace: unknown[] = [];
    for (let i = 0; i < 13; i++) {
      f.destinations[i].copy = vi.fn((...args: unknown[]) => {
        trace.push([i, ...args]);
        return true;
      });
    }
    copyGsTileTextureRange(f.source, f.destination, 5, 9, 47);
    const plan = arena.planLinearTextureCopies(7, 9, 5, 8, 8, 9, 47);
    expect(trace).toEqual(f.sources.flatMap((texture, i) =>
      plan.map(options => [i, texture, options]),
    ));
  });

  it("stops on the first rejected GPU copy, preserving error behavior", () => {
    const f = fixture();
    f.destinations[0].copy.mockReturnValueOnce(false);
    expect(() => copyGsTileTextureRange(f.source, f.destination, 3, 2, 7)).toThrow("stream 0 copy failed");
    expect(f.destinations[0].copy).toHaveBeenCalledTimes(1);
    expect(f.destinations[1].copy).not.toHaveBeenCalled();
  });
});
