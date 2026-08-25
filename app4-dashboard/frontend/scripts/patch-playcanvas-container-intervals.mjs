import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const JS_MARKER = "DroneAI stable GSplat container intervals";
const JS_BOUNDS_MARKER = "DroneAI conservative interval spheres v2";
const JS_ANCHOR = /\n([ \t]+)hide\(\) \{/g;
const JS_METHOD = `
	// ${JS_MARKER}
	setActiveSplatIntervals(intervals) {
		const placement = this._placement;
		if (!placement || !this.resource) {
			throw new Error("GSplat resource must be attached before setting active intervals");
		}
		const capacity = this.resource.maxSplats ?? this.resource.numSplats;
		let previousEnd = 0;
		placement.intervals.clear();
		placement.intervalBounds = null;
		const intervalBounds = [];
		for (let index = 0; index < intervals.length; index++) {
			const { start, count, bounds } = intervals[index];
			const end = start + count;
			if (!Number.isSafeInteger(start) || !Number.isSafeInteger(count) || start < previousEnd || count < 1 || end > capacity) {
				placement.intervals.clear();
				placement.intervalBounds = null;
				throw new Error("GSplat active intervals are invalid, overlapping or out of bounds");
			}
			const min = bounds?.min;
			const max = bounds?.max;
			if (!min || !max || min.length !== 3 || max.length !== 3 || !min.every(Number.isFinite) || !max.every(Number.isFinite) || min.some((value, axis) => value > max[axis])) {
				placement.intervals.clear();
				placement.intervalBounds = null;
				throw new Error("GSplat active interval bounds are invalid");
			}
			// ${JS_BOUNDS_MARKER}. The center is quantized first; the radius is
			// then inflated beyond float32 rounding so culling stays conservative.
			const x = Math.fround((min[0] + max[0]) * 0.5);
			const y = Math.fround((min[1] + max[1]) * 0.5);
			const z = Math.fround((min[2] + max[2]) * 0.5);
			const dx = Math.max(Math.abs(min[0] - x), Math.abs(max[0] - x));
			const dy = Math.max(Math.abs(min[1] - y), Math.abs(max[1] - y));
			const dz = Math.max(Math.abs(min[2] - z), Math.abs(max[2] - z));
			const radius = Math.fround(Math.hypot(dx, dy, dz) * 1.000001 + 1e-6);
			if (![x, y, z, radius].every(Number.isFinite)) {
				placement.intervals.clear();
				placement.intervalBounds = null;
				throw new Error("GSplat active interval sphere is not finite");
			}
			placement.intervals.set(index, { x: start, y: end - 1 });
			intervalBounds.push(x, y, z, radius);
			previousEnd = end;
		}
		placement.intervalBounds = intervalBounds;
		placement.markDirty();
		const layers = this.system.app.scene.layers;
		for (let index = 0; index < this._layers.length; index++) {
			const layer = layers.getLayerById(this._layers[index]);
			if (layer) layer.gsplatPlacementsDirty = true;
		}
	}
`;
const INFO_MARKER = "DroneAI non-octree per-interval bounds";
const LEGACY_INFO_PATCH =
  /this\.activeSplats = totalCount;(?:[ \t]*|\n[ \t]+)\/\/ DroneAI non-octree interval bounds\n[ \t]+this\.numBoundsEntries = 1;/g;
const WORLD_MARKER = "DroneAI contiguous non-octree interval offsets";
const WORLD_ANCHOR =
  /\n([ \t]+)const block = allocationMap\.get\(splat\.allocId\);\n([ \t]+)if \(block\) \{\n([ \t]+)intervalOffsets\.push\(block\.offset\);/g;
const RENDER_MARKER = "DroneAI non-octree interval full upload";
const RENDER_ANCHOR = "if (numIntervals === 0) {";
const RENDER_INNER_ALLOC_IDS =
  /\n([ \t]+)const baseOffset = writeOffset;\n([ \t]+)const allocIds = splatInfo\.intervalAllocIds;\n/g;
const RENDER_OUTER_ALLOC_IDS = new RegExp(
  `const numIntervals = intervals\\.length / 2;\\n[ \\t]+const allocIds = splatInfo\\.intervalAllocIds;\\n[ \\t]+/\\* ${RENDER_MARKER} \\*/`,
  "g",
);

const DTS_MARKER = "setActiveSplatIntervals";
const DTS_BOUNDS_MARKER = "bounds: { min: readonly";
const DTS_ANCHOR = "    hide(): void;";
const DTS_BUNDLE_CLASS_ANCHOR =
  "declare class GSplatComponent extends Component {";
const DTS_METHOD = `    /** Set sorted active source ranges and conservative local-space bounds. */
    setActiveSplatIntervals(intervals: ReadonlyArray<{ start: number; count: number; bounds: { min: readonly [number, number, number]; max: readonly [number, number, number] } }>): void;
`;

export const patchPlayCanvasContainerIntervalsSource = (source) => {
  const markerCount = source.split(JS_MARKER).length - 1;
  const boundsMarkerCount = source.split(JS_BOUNDS_MARKER).length - 1;
  const anchors = [...source.matchAll(JS_ANCHOR)];
  const anchorCount = anchors.length;
  if (markerCount === 1 && boundsMarkerCount === 1 && anchorCount === 1)
    return source;
  if (markerCount === 1 && boundsMarkerCount === 0 && anchorCount === 1) {
    const indentation = anchors[0][1];
    const legacyMethod = new RegExp(
      `\\n${indentation}\\/\\/ ${JS_MARKER}[\\s\\S]*?\\n${indentation}hide\\(\\) \\{`,
    );
    const method = JS_METHOD.replaceAll("\t", indentation);
    const upgraded = source.replace(
      legacyMethod,
      `${method}\n${indentation}hide() {`,
    );
    if (upgraded === source) {
      throw new Error("Unexpected PlayCanvas legacy interval JS state");
    }
    return upgraded;
  }
  if (markerCount === 0 && anchorCount === 1) {
    const indentation = anchors[0][1];
    const method = JS_METHOD.replaceAll("\t", indentation);
    return source.replace(JS_ANCHOR, `${method}\n${indentation}hide() {`);
  }
  throw new Error(
    `Unexpected PlayCanvas container interval JS state: marker=${markerCount}, bounds=${boundsMarkerCount}, anchor=${anchorCount}`,
  );
};

export const patchPlayCanvasContainerIntervalInfoSource = (source) => {
  const markerCount = source.split(INFO_MARKER).length - 1;
  if (markerCount === 1) return source;
  if (markerCount !== 0) {
    throw new Error(`Unexpected PlayCanvas interval info marker=${markerCount}`);
  }
  let patched = source.replace(
    LEGACY_INFO_PATCH,
    "this.activeSplats = totalCount;",
  );
  const constructorAnchor =
    /\n([ \t]+)this\.nodeInfos = nodeInfos;\n\1this\.updateIntervals\(placement\.intervals\);/g;
  const constructorMatches = [...patched.matchAll(constructorAnchor)];
  if (constructorMatches.length !== 1) {
    throw new Error("Unexpected PlayCanvas interval info constructor anchor");
  }
  patched = patched.replace(
    constructorAnchor,
    (_match, indentation) =>
      `\n${indentation}this.nodeInfos = nodeInfos;\n${indentation}this.intervalBounds = placement.intervalBounds ?? null;\n${indentation}this.updateIntervals(placement.intervals);`,
  );
  const branchAnchor =
    /\n([ \t]+)if \(this\.octreeNodes\) \{\n([ \t]+)this\.activeSplats = totalCount;\n\2this\.numBoundsEntries = this\.octreeNodes\.length;\n\1} else if \(totalCount === this\.numSplats\) \{\n\2this\.intervals\.length = 0;\n\1} else \{\n\2this\.activeSplats = totalCount;\n\1}/g;
  const branchMatches = [...patched.matchAll(branchAnchor)];
  if (branchMatches.length !== 1) {
    throw new Error("Unexpected PlayCanvas interval info branch anchor");
  }
  patched = patched.replace(
    branchAnchor,
    (_match, statementIndentation, bodyIndentation) =>
      `\n${statementIndentation}if (this.octreeNodes) {\n${bodyIndentation}this.activeSplats = totalCount;\n${bodyIndentation}this.numBoundsEntries = this.octreeNodes.length;\n${statementIndentation}} else if (this.intervalBounds?.length === intervals.size * 4) {\n${bodyIndentation}// ${INFO_MARKER}\n${bodyIndentation}this.activeSplats = totalCount;\n${bodyIndentation}this.numBoundsEntries = intervals.size;\n${bodyIndentation}for (let index = 0; index < intervals.size; index++) this.intervalNodeIndices.push(index);\n${statementIndentation}} else if (totalCount === this.numSplats) {\n${bodyIndentation}this.intervals.length = 0;\n${statementIndentation}} else {\n${bodyIndentation}this.activeSplats = totalCount;\n${bodyIndentation}this.numBoundsEntries = 1;\n${statementIndentation}}`,
  );
  const boundsAnchor =
    /\n([ \t]+)} else \{\n([ \t]+)const aabb = this\.resource\.aabb;\n\2const he = aabb\.halfExtents;\n\2const r = Math\.sqrt\(he\.x \* he\.x \+ he\.y \* he\.y \+ he\.z \* he\.z\);\n\2data\[offset\+\+\] = aabb\.center\.x;\n\2data\[offset\+\+\] = aabb\.center\.y;\n\2data\[offset\+\+\] = aabb\.center\.z;\n\2data\[offset\+\+\] = r;\n\1}/g;
  const boundsMatches = [...patched.matchAll(boundsAnchor)];
  if (boundsMatches.length !== 1) {
    throw new Error("Unexpected PlayCanvas interval info bounds anchor");
  }
  patched = patched.replace(
    boundsAnchor,
    (_match, statementIndentation, bodyIndentation) =>
      `\n${statementIndentation}} else if (this.intervalBounds) {\n${bodyIndentation}data.set(this.intervalBounds, offset);\n${statementIndentation}} else {\n${bodyIndentation}const aabb = this.resource.aabb;\n${bodyIndentation}const he = aabb.halfExtents;\n${bodyIndentation}const r = Math.sqrt(he.x * he.x + he.y * he.y + he.z * he.z);\n${bodyIndentation}data[offset++] = aabb.center.x;\n${bodyIndentation}data[offset++] = aabb.center.y;\n${bodyIndentation}data[offset++] = aabb.center.z;\n${bodyIndentation}data[offset++] = r;\n${statementIndentation}}`,
  );
  return patched;
};

export const patchPlayCanvasContainerIntervalWorldSource = (source) => {
  const markerCount = source.split(WORLD_MARKER).length - 1;
  const anchors = [...source.matchAll(WORLD_ANCHOR)];
  if (markerCount === 1 && anchors.length === 0) return source;
  if (markerCount === 0 && anchors.length === 1) {
    const [match, declarationIndentation, , statementIndentation] = anchors[0];
    const indentationUnit = statementIndentation.slice(
      declarationIndentation.length,
    );
    if (!indentationUnit) {
      throw new Error("PlayCanvas interval world indentation is invalid");
    }
    const nestedIndentation = statementIndentation + indentationUnit;
    const doubleNestedIndentation = nestedIndentation + indentationUnit;
    const replacement = `${match.slice(0, match.lastIndexOf("intervalOffsets.push"))}// ${WORLD_MARKER}\n${statementIndentation}if (numIntervals > 0) {\n${nestedIndentation}let intervalOffset = block.offset;\n${nestedIndentation}for (let j = 0; j < numIntervals; j++) {\n${doubleNestedIndentation}intervalOffsets.push(intervalOffset);\n${doubleNestedIndentation}intervalOffset += intervals[j * 2 + 1] - intervals[j * 2];\n${nestedIndentation}}\n${statementIndentation}} else {\n${nestedIndentation}intervalOffsets.push(block.offset);\n${statementIndentation}}`;
    return source.replace(WORLD_ANCHOR, replacement);
  }
  throw new Error(
    `Unexpected PlayCanvas interval world state: marker=${markerCount}, anchor=${anchors.length}`,
  );
};

export const patchPlayCanvasContainerIntervalRenderSource = (source) => {
  const markerCount = source.split(RENDER_MARKER).length - 1;
  const anchorCount = source.split(RENDER_ANCHOR).length - 1;
  const innerDeclarations = [...source.matchAll(RENDER_INNER_ALLOC_IDS)];
  const outerDeclarations = [...source.matchAll(RENDER_OUTER_ALLOC_IDS)];
  if (
    markerCount === 1 &&
    anchorCount === 0 &&
    innerDeclarations.length === 0 &&
    outerDeclarations.length === 1
  ) {
    return source;
  }
  if (markerCount === 0 && anchorCount === 1) {
    if (innerDeclarations.length !== 1 || outerDeclarations.length !== 0) {
      throw new Error("Unexpected PlayCanvas interval render allocId state");
    }
    const withoutInner = source.replace(
      RENDER_INNER_ALLOC_IDS,
      (_match, baseIndentation) =>
        `\n${baseIndentation}const baseOffset = writeOffset;\n`,
    );
    return withoutInner.replace(
      RENDER_ANCHOR,
      `const allocIds = splatInfo.intervalAllocIds;\n\t\t\t\t/* ${RENDER_MARKER} */\n\t\t\t\tif (numIntervals === 0 || allocIds.length !== numIntervals) {`,
    );
  }
  if (
    markerCount === 1 &&
    anchorCount === 0 &&
    innerDeclarations.length === 1 &&
    outerDeclarations.length === 0
  ) {
    const withoutInner = source.replace(
      RENDER_INNER_ALLOC_IDS,
      (_match, baseIndentation) =>
        `\n${baseIndentation}const baseOffset = writeOffset;\n`,
    );
    return withoutInner.replace(
      `/* ${RENDER_MARKER} */`,
      `const allocIds = splatInfo.intervalAllocIds;\n\t\t\t\t/* ${RENDER_MARKER} */`,
    );
  }
  throw new Error(
    `Unexpected PlayCanvas interval render state: marker=${markerCount}, anchor=${anchorCount}, inner=${innerDeclarations.length}, outer=${outerDeclarations.length}`,
  );
};

export const patchPlayCanvasContainerIntervalsTypes = (source) => {
  const markerCount = source.split(DTS_MARKER).length - 1;
  const boundsMarkerCount = source.split(DTS_BOUNDS_MARKER).length - 1;
  const anchorCount = source.split(DTS_ANCHOR).length - 1;
  if (markerCount === 1 && boundsMarkerCount === 1 && anchorCount === 1)
    return source;
  if (markerCount === 1 && boundsMarkerCount === 0 && anchorCount === 1) {
    const upgraded = source.replace(
      /    \/\*\* Set sorted, non-overlapping active source ranges for a procedural container\. \*\/\n    setActiveSplatIntervals\([^\n]+\): void;\n/,
      DTS_METHOD,
    );
    if (upgraded === source) {
      throw new Error("Unexpected PlayCanvas legacy interval type");
    }
    return upgraded;
  }
  if (markerCount === 0 && anchorCount === 1) {
    return source.replace(DTS_ANCHOR, `${DTS_METHOD}${DTS_ANCHOR}`);
  }
  throw new Error(
    `Unexpected PlayCanvas container interval type state: marker=${markerCount}, bounds=${boundsMarkerCount}, anchor=${anchorCount}`,
  );
};

export const patchPlayCanvasContainerIntervalsBundleTypes = (source) => {
  const classStart = source.indexOf(DTS_BUNDLE_CLASS_ANCHOR);
  const markerCount = source.split(DTS_MARKER).length - 1;
  const boundsMarkerCount = source.split(DTS_BOUNDS_MARKER).length - 1;
  if (classStart < 0) {
    throw new Error("PlayCanvas GSplatComponent bundle type anchor is missing");
  }
  const classEnd = source.indexOf("\n}\n", classStart);
  if (classEnd < 0) {
    throw new Error("PlayCanvas GSplatComponent bundle type end is missing");
  }
  const component = source.slice(classStart, classEnd);
  const anchorCount = component.split(DTS_ANCHOR).length - 1;
  if (markerCount === 1 && boundsMarkerCount === 1 && anchorCount === 1)
    return source;
  if (markerCount === 1 && boundsMarkerCount === 0 && anchorCount === 1) {
    const upgraded = component.replace(
      /    \/\*\* Set sorted, non-overlapping active source ranges for a procedural container\. \*\/\n    setActiveSplatIntervals\([^\n]+\): void;\n/,
      DTS_METHOD,
    );
    if (upgraded === component) {
      throw new Error("Unexpected PlayCanvas legacy aggregate interval type");
    }
    return `${source.slice(0, classStart)}${upgraded}${source.slice(classEnd)}`;
  }
  if (markerCount === 0 && anchorCount === 1) {
    const patched = component.replace(DTS_ANCHOR, `${DTS_METHOD}${DTS_ANCHOR}`);
    return `${source.slice(0, classStart)}${patched}${source.slice(classEnd)}`;
  }
  throw new Error(
    `Unexpected PlayCanvas container interval bundle type state: marker=${markerCount}, bounds=${boundsMarkerCount}, anchor=${anchorCount}`,
  );
};

const patchInstalledPlayCanvas = async () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const packageRoot = resolve(frontendRoot, "node_modules/playcanvas");
  const packageJson = JSON.parse(
    await readFile(resolve(packageRoot, "package.json"), "utf8"),
  );
  if (packageJson.version !== PLAYCANVAS_VERSION) {
    throw new Error(
      `PlayCanvas ${packageJson.version} is not the audited ${PLAYCANVAS_VERSION} release`,
    );
  }

  const sources = [
    "build/playcanvas/src/framework/components/gsplat/component.js",
    "build/playcanvas.dbg/src/framework/components/gsplat/component.js",
    "build/playcanvas.prf/src/framework/components/gsplat/component.js",
  ];
  let changed = 0;
  for (const relativePath of sources) {
    const path = resolve(packageRoot, relativePath);
    const source = await readFile(path, "utf8");
    const patched = patchPlayCanvasContainerIntervalsSource(source);
    if (patched !== source) {
      await writeFile(path, patched, "utf8");
      changed += 1;
    }
  }
  const sourcePatches = [
    [
      "src/scene/gsplat-unified/gsplat-info.js",
      patchPlayCanvasContainerIntervalInfoSource,
    ],
    [
      "src/scene/gsplat-unified/gsplat-world-state.js",
      patchPlayCanvasContainerIntervalWorldSource,
    ],
    [
      "src/scene/gsplat-unified/gsplat-work-buffer-render-pass.js",
      patchPlayCanvasContainerIntervalRenderSource,
    ],
  ];
  for (const build of ["playcanvas", "playcanvas.dbg", "playcanvas.prf"]) {
    for (const [relativePath, transform] of sourcePatches) {
      const path = resolve(packageRoot, "build", build, relativePath);
      const source = await readFile(path, "utf8");
      const patched = transform(source);
      if (patched !== source) {
        await writeFile(path, patched, "utf8");
        changed += 1;
      }
    }
  }
  const typesPath = resolve(
    packageRoot,
    "build/playcanvas/src/framework/components/gsplat/component.d.ts",
  );
  const types = await readFile(typesPath, "utf8");
  const patchedTypes = patchPlayCanvasContainerIntervalsTypes(types);
  if (patchedTypes !== types) {
    await writeFile(typesPath, patchedTypes, "utf8");
    changed += 1;
  }
  const bundleTypesPath = resolve(packageRoot, "build/playcanvas.d.ts");
  const bundleTypes = await readFile(bundleTypesPath, "utf8");
  const patchedBundleTypes =
    patchPlayCanvasContainerIntervalsBundleTypes(bundleTypes);
  if (patchedBundleTypes !== bundleTypes) {
    await writeFile(bundleTypesPath, patchedBundleTypes, "utf8");
    changed += 1;
  }
  process.stdout.write(
    `PlayCanvas ${PLAYCANVAS_VERSION} container interval patch: ${changed} artifact(s) changed\n`,
  );
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await patchInstalledPlayCanvas();
}
