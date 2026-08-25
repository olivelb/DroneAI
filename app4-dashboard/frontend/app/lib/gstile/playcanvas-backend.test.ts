import { describe, expect, it, vi } from "vitest";
import {
  completeLodTargetPlan,
  configurePlayCanvasGsplatArenaResource,
  configureHighQualityGsplatRendering,
  coordinateFrameCameraPosition,
  fitOrbitDistance,
  fitOrbitDistanceInFrame,
  gstileGpuAssembly,
  gstileOpacityMode,
  gstileSortMode,
  gstileLodSelectionKey,
  gstileLodUpdateDelayMilliseconds,
  gstileTransformPrecision,
  gstileVerticalFovDegrees,
  lodTransitionCounts,
  lodProxyCoverage,
  orbitCameraBasis,
  planLodTransitions,
  prioritizeLodLoads,
  releasePlayCanvasTextureCpuSources,
  resetPlayCanvasGsplatManagers,
  panOrbitTarget,
} from "./playcanvas-backend";
import type { GaussianViewFrame } from "./backend";
import type { GsTileManifest, GsTileNode } from "./contracts";

describe("GSTile transform precision", () => {
  it("uses PlayCanvas' validated native packing unless float32 is explicit", () => {
    expect(gstileTransformPrecision(null)).toBe("packed");
    expect(gstileTransformPrecision("packed")).toBe("packed");
    expect(gstileTransformPrecision("float32")).toBe("float32");
  });
});

describe("GSTile GPU assembly", () => {
  it("keeps the exact monolithic path as the default", () => {
    expect(gstileGpuAssembly(null)).toBe("merged");
    expect(gstileGpuAssembly("merged")).toBe("merged");
    expect(gstileGpuAssembly("unknown")).toBe("merged");
  });

  it("allows the two diagnostic multi-resource modes only explicitly", () => {
    expect(gstileGpuAssembly("tiled")).toBe("tiled");
    expect(gstileGpuAssembly("incremental")).toBe("incremental");
  });
});

describe("GSTile opacity mode", () => {
  it("keeps directional opacity by default and exposes exact diagnostics", () => {
    expect(gstileOpacityMode(null)).toBe("directional");
    expect(gstileOpacityMode("unknown")).toBe("directional");
    expect(gstileOpacityMode("base")).toBe("base");
    expect(gstileOpacityMode("directional-no-reveal")).toBe(
      "directional-no-reveal",
    );
  });
});

describe("GSTile sort mode", () => {
  it("keeps GPU sorting by default and allows an explicit CPU diagnostic", () => {
    expect(gstileSortMode(null)).toBe("gpu");
    expect(gstileSortMode("unknown")).toBe("gpu");
    expect(gstileSortMode("cpu")).toBe("cpu");
  });
});

describe("GSTile LOD selection identity", () => {
  it("does not rebuild a cut when camera priority only reorders the same nodes", () => {
    expect(gstileLodSelectionKey(["r10", "r0", "r11"])).toBe(
      gstileLodSelectionKey(["r11", "r10", "r0"]),
    );
    expect(gstileLodSelectionKey(["r0", "r10"])).not.toBe(
      gstileLodSelectionKey(["r0", "r11"]),
    );
  });
});

describe("GSTile LOD update delay", () => {
  it("starts refinement promptly after interaction settles", () => {
    expect(gstileLodUpdateDelayMilliseconds(undefined)).toBe(120);
    expect(gstileLodUpdateDelayMilliseconds(0)).toBe(0);
  });

  it("rejects invalid delays instead of hiding configuration errors", () => {
    expect(() => gstileLodUpdateDelayMilliseconds(-1)).toThrow(/0 to 5000/);
    expect(() => gstileLodUpdateDelayMilliseconds(5_001)).toThrow(/0 to 5000/);
    expect(() => gstileLodUpdateDelayMilliseconds(1.5)).toThrow(/0 to 5000/);
  });
});

describe("GSTile vertical FOV", () => {
  it("uses a less distorted default and clamps live values", () => {
    expect(gstileVerticalFovDegrees(null)).toBe(42);
    expect(gstileVerticalFovDegrees("35")).toBe(35);
    expect(gstileVerticalFovDegrees(10)).toBe(20);
    expect(gstileVerticalFovDegrees(120)).toBe(80);
    expect(gstileVerticalFovDegrees("invalid")).toBe(42);
  });
});

describe("PlayCanvas unified world reset", () => {
  it("destroys every unique manager and clears main and shadow slots", () => {
    const main = { destroy: vi.fn() };
    const shadow = { destroy: vi.fn() };
    type LayerData = {
      gsplatManager: typeof main | null;
      gsplatManagerShadow: typeof main | null;
    };
    const firstLayer: LayerData = {
      gsplatManager: main,
      gsplatManagerShadow: shadow,
    };
    const secondLayer: LayerData = {
      gsplatManager: main,
      gsplatManagerShadow: null,
    };
    const director = {
      camerasMap: new Map([
        [
          "camera",
          {
            layersMap: new Map([
              ["world", firstLayer],
              ["overlay", secondLayer],
            ]),
          },
        ],
      ]),
    };

    expect(resetPlayCanvasGsplatManagers(director)).toBe(2);
    expect(main.destroy).toHaveBeenCalledTimes(1);
    expect(shadow.destroy).toHaveBeenCalledTimes(1);
    expect(firstLayer).toEqual({
      gsplatManager: null,
      gsplatManagerShadow: null,
    });
    expect(secondLayer).toEqual({
      gsplatManager: null,
      gsplatManagerShadow: null,
    });
  });

  it("is a no-op before PlayCanvas creates a director", () => {
    expect(resetPlayCanvasGsplatManagers(null)).toBe(0);
  });
});

describe("PlayCanvas promoted GSTile arena", () => {
  it("changes only the active count and centers version", () => {
    const resource = { centersVersion: 4 };
    const data = { numSplats: 10 };
    const arena = configurePlayCanvasGsplatArenaResource(
      resource,
      data,
      10,
      6,
    );

    expect(arena).toBe(resource);
    expect(arena.maxSplats).toBe(10);
    expect(data.numSplats).toBe(6);
    expect(resource.centersVersion).toBe(4);

    arena.update(8);
    expect(data.numSplats).toBe(8);
    expect(resource.centersVersion).toBe(5);

    arena.update(99, false);
    expect(data.numSplats).toBe(10);
    expect(resource.centersVersion).toBe(5);
  });

  it("rejects an active count outside the allocated capacity", () => {
    expect(() =>
      configurePlayCanvasGsplatArenaResource(
        { centersVersion: 0 },
        { numSplats: 1 },
        10,
        11,
      ),
    ).toThrow(/counts are invalid/);
  });

  it("releases only uploaded typed-array texture levels", () => {
    const typed = new Float32Array(12);
    const texture = {
      _levels: [typed] as unknown[],
      getSource() {
        return this._levels[0];
      },
    };
    const image = {
      _levels: [{ width: 1 }],
      getSource() {
        return this._levels[0];
      },
    };

    expect(releasePlayCanvasTextureCpuSources([texture, image])).toBe(
      typed.byteLength,
    );
    expect(texture._levels[0]).toBeNull();
    expect(image._levels[0]).toEqual({ width: 1 });
  });
});

describe("PlayCanvas orbit camera helpers", () => {
  it("fits both vertical and horizontal bounds into the perspective frustum", () => {
    const distance = fitOrbitDistance([-3.5, -7, 0], [3.5, 5, 3], 55, 2, 1);
    const availableHalfHeight =
      (distance - 1.5) * Math.tan((55 * Math.PI) / 360);
    const availableHalfWidth = availableHalfHeight * 2;
    expect(availableHalfHeight).toBeGreaterThanOrEqual(6);
    expect(availableHalfWidth).toBeGreaterThanOrEqual(3.5);
  });

  it("uses the horizontal constraint for a wide model", () => {
    const distance = fitOrbitDistance([-10, -1, 0], [10, 1, 2], 60, 1, 1);
    expect(distance).toBeCloseTo(1 + 10 / Math.tan(Math.PI / 6));
  });

  it("pans in screen-aligned horizontal and vertical directions", () => {
    const target = panOrbitTarget([0, 0, 0], 0, 0, 10, 100, 50, 1000, 90);
    expect(target[0]).toBeCloseTo(-2);
    expect(target[1]).toBeCloseTo(1);
    expect(target[2]).toBeCloseTo(0);
  });

  it("keeps panning screen-aligned after orbiting", () => {
    const target = panOrbitTarget(
      [1, 2, 3],
      Math.PI / 2,
      0,
      10,
      100,
      0,
      1000,
      90,
    );
    expect(target[0]).toBeCloseTo(1);
    expect(target[1]).toBeCloseTo(2);
    expect(target[2]).toBeCloseTo(5);
  });

  it("does not produce non-finite motion for a hidden or collapsed canvas", () => {
    const target = panOrbitTarget([0, 0, 0], 0, 0, 1, 1, 1, 0, 55);
    expect(target.every(Number.isFinite)).toBe(true);
  });

  const facadeFrame: GaussianViewFrame = {
    kind: "facade",
    right: [0, 1, 0],
    up: [0, 0, 1],
    outward: [1, 0, 0],
  };

  it("frames bounds in the recommended facade axes", () => {
    const distance = fitOrbitDistanceInFrame(
      [-1, -5, -10],
      [1, 5, 10],
      facadeFrame,
      90,
      1,
      1,
    );
    expect(distance).toBeCloseTo(11);
  });

  it("starts on the recommended outward normal with facade up", () => {
    const basis = orbitCameraBasis(facadeFrame, 0, 0);
    expect(basis.offset).toEqual([1, 0, 0]);
    expect(basis.right).toEqual([0, 1, 0]);
    expect(basis.up).toEqual([0, 0, 1]);
  });

  it("pans in facade screen coordinates", () => {
    const target = panOrbitTarget(
      [0, 0, 0],
      0,
      0,
      10,
      100,
      50,
      1000,
      90,
      facadeFrame,
    );
    expect(target[0]).toBeCloseTo(0);
    expect(target[1]).toBeCloseTo(-2);
    expect(target[2]).toBeCloseTo(1);
  });
});

describe("GSTile proxy coverage", () => {
  const proxyNode: GsTileNode = {
    id: "r",
    bounds: { min: [0, 0, 0], max: [8, 4, 0.25] },
    gaussianCount: 800_000,
    lodTile: {
      pack: "lod-r",
      byteOffset: 32,
      byteLength: 96_000,
      recordCount: 1_000,
      sha256: "a".repeat(64),
      quantization: {} as NonNullable<GsTileNode["lodTile"]>["quantization"],
    },
  };

  it("expands a sparse proxy up to its bounded surface spacing", () => {
    const coverage = lodProxyCoverage(proxyNode);
    expect(coverage.multiplier).toBe(800);
    expect(coverage.maximumScale).toBeCloseTo(
      Math.hypot(8, 4, 0.25) / Math.sqrt(1_000),
    );
  });

  it("does not inflate moment-matched proxies a second time", () => {
    expect(lodProxyCoverage(proxyNode, false)).toEqual({
      multiplier: 1,
      maximumScale: Number.MAX_VALUE,
    });
  });

  it("keeps exact leaf representations unchanged", () => {
    const exact = {
      ...proxyNode,
      tile: proxyNode.lodTile,
    } satisfies GsTileNode;
    expect(lodProxyCoverage(exact)).toEqual({
      multiplier: 1,
      maximumScale: Number.MAX_VALUE,
    });
  });
});
describe("PlayCanvas high-quality Gaussian rendering", () => {
  it("evaluates directional opacity in the same absolute frame as splat centers", () => {
    expect(
      coordinateFrameCameraPosition([2.5, -4, 8], [638_000, 6_215_000, 123]),
    ).toEqual([638_002.5, 6_214_996, 131]);
  });

  it("preserves FastGS footprints and enables low-contribution rendering", () => {
    const settings = {
      antiAlias: false,
      alphaClip: 0.3,
      alphaClipForward: 1 / 255,
      colorUpdateAngle: 4,
      dataFormat: "compact",
      minContribution: 3,
      minPixelSize: 2,
      radialSorting: false,
      renderer: 0,
    };

    configureHighQualityGsplatRendering(settings, {
      dataFormat: "large",
      renderer: 2,
    });

    expect(settings).toEqual({
      antiAlias: false,
      alphaClip: 1 / 255,
      alphaClipForward: 1 / 255,
      colorUpdateAngle: 0,
      dataFormat: "large",
      minContribution: 0.05,
      minPixelSize: 0.5,
      radialSorting: false,
      renderer: 2,
    });
  });
});

describe("GSTile progressive LOD transitions", () => {
  const manifest = {
    root: "r",
    nodes: [
      { id: "r", children: ["a", "b"] },
      { id: "a", children: ["a0", "a1"] },
      { id: "b" },
      { id: "a0" },
      { id: "a1" },
    ],
  } as GsTileManifest;

  it("groups refinement and coarsening into atomic subtree swaps", () => {
    expect(planLodTransitions(manifest, ["r"], ["a", "b"])).toEqual([
      {
        addNodeIds: ["a", "b"],
        removeNodeIds: ["r"],
      },
    ]);
    expect(planLodTransitions(manifest, ["a", "b"], ["a0", "a1", "b"])).toEqual(
      [
        {
          addNodeIds: ["a0", "a1"],
          removeNodeIds: ["a"],
        },
      ],
    );
    expect(planLodTransitions(manifest, ["a0", "a1", "b"], ["a", "b"])).toEqual(
      [
        {
          addNodeIds: ["a"],
          removeNodeIds: ["a0", "a1"],
        },
      ],
    );
  });

  it("reveals initial target nodes independently as they become ready", () => {
    expect(planLodTransitions(manifest, [], ["a", "b"])).toEqual([
      { addNodeIds: ["a"], removeNodeIds: [] },
      { addNodeIds: ["b"], removeNodeIds: [] },
    ]);
  });

  it("loads every sibling of the highest-impact replacement before the next branch", () => {
    const transitions = [
      { addNodeIds: ["b0", "b1"], removeNodeIds: ["b"] },
      { addNodeIds: ["a0", "a1"], removeNodeIds: ["a"] },
    ];
    expect(
      prioritizeLodLoads(
        transitions,
        ["a0", "b0", "a1", "b1", "stable"],
        ["a", "b", "stable"],
      ),
    ).toEqual(["a0", "a1", "b0", "b1"]);
  });

  it("does not count an already resident target twice during an atomic swap", () => {
    const resident = new Map([
      ["r", 1_000],
      ["a", 4_000],
    ]);
    const staged = new Map([["b", 4_000]]);

    expect(
      lodTransitionCounts(
        { addNodeIds: ["a", "b"], removeNodeIds: ["r"] },
        (nodeId) => resident.get(nodeId),
        (nodeId) => staged.get(nodeId),
      ),
    ).toEqual({ add: 4_000, remove: 1_000 });
  });

  it("falls back to one complete target commit when local swaps cannot fit", () => {
    const resident = new Map([
      ["r", 1_000],
      ["a", 4_000],
    ]);
    const staged = new Map([["b", 4_000]]);

    expect(
      completeLodTargetPlan(
        resident.keys(),
        ["a", "b"],
        (nodeId) => resident.get(nodeId),
        (nodeId) => staged.get(nodeId),
      ),
    ).toEqual({
      complete: true,
      gaussianCount: 8_000,
      addNodeIds: ["b"],
      removeNodeIds: ["r"],
    });
  });

  it("refuses an incomplete final LOD target", () => {
    const resident = new Map([["a", 4_000]]);
    expect(
      completeLodTargetPlan(
        resident.keys(),
        ["a", "b"],
        (nodeId) => resident.get(nodeId),
        () => undefined,
      ),
    ).toMatchObject({ complete: false });
  });
});
