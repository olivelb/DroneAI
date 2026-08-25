import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const MESH_MARKER = "DroneAI circumscribed hexagon GSplat footprint";
const MESH_ANCHOR =
  /\n([ \t]+)static get instanceSize\(\) \{/g;
const ORIGINAL_HYBRID_MESH =
  "const mesh = GSplatResourceBase.createMesh(this.device);";
const HEXAGON_HYBRID_MESH =
  "const mesh = GSplatResourceBase.createHybridMesh(this.device);";
const ORIGINAL_INDEX_COUNT =
  "const INDEX_COUNT = 6 * GSplatResourceBase.instanceSize;";
const HEXAGON_INDEX_COUNT =
  "const INDEX_COUNT = 12 * GSplatResourceBase.instanceSize;";

// Inflate a regular hexagon whose apothem is one beyond float32 rounding.
const FOOTPRINT_SCALE = 1.000001;
const FOOTPRINT_Y = FOOTPRINT_SCALE / Math.sqrt(3);
const FOOTPRINT_RADIUS = FOOTPRINT_Y * 2;

export const GSPLAT_HEXAGON_FOOTPRINT = Object.freeze([
  Object.freeze([FOOTPRINT_SCALE, FOOTPRINT_Y]),
  Object.freeze([0, FOOTPRINT_RADIUS]),
  Object.freeze([-FOOTPRINT_SCALE, FOOTPRINT_Y]),
  Object.freeze([-FOOTPRINT_SCALE, -FOOTPRINT_Y]),
  Object.freeze([0, -FOOTPRINT_RADIUS]),
  Object.freeze([FOOTPRINT_SCALE, -FOOTPRINT_Y]),
]);

const positionRows = GSPLAT_HEXAGON_FOOTPRINT.map(
  ([x, y]) => `\t\t\t\t${x},\n\t\t\t\t${y},\n\t\t\t\ti`,
).join(",\n");

const HEXAGON_MESH_METHOD = `
\tstatic createHybridMesh(device) {
\t\t// ${MESH_MARKER}. It strictly contains the unit Gaussian disk while
\t\t// reducing the square footprint area by about 13.4 percent.
\t\tconst splatInstanceSize = GSplatResourceBase.instanceSize;
\t\tconst verticesPerSplat = 6;
\t\tconst indicesPerSplat = 12;
\t\tconst meshPositions = new Float32Array(verticesPerSplat * 3 * splatInstanceSize);
\t\tconst meshIndices = new Uint32Array(indicesPerSplat * splatInstanceSize);
\t\tfor (let i = 0; i < splatInstanceSize; ++i) {
\t\t\tmeshPositions.set([
${positionRows}
\t\t\t], i * verticesPerSplat * 3);
\t\t\tconst b = i * verticesPerSplat;
\t\t\tmeshIndices.set([
\t\t\t\t0 + b, 1 + b, 2 + b,
\t\t\t\t0 + b, 2 + b, 3 + b,
\t\t\t\t0 + b, 3 + b, 4 + b,
\t\t\t\t0 + b, 4 + b, 5 + b
\t\t\t], i * indicesPerSplat);
\t\t}
\t\tconst mesh = new Mesh(device);
\t\tmesh.setPositions(meshPositions, 3);
\t\tmesh.setIndices(meshIndices);
\t\tmesh.update();
\t\treturn mesh;
\t}
`;

export const patchGsplatHexagonMeshSource = (source) => {
  const markerCount = source.split(MESH_MARKER).length - 1;
  const anchors = [...source.matchAll(MESH_ANCHOR)];
  if (markerCount === 1 && anchors.length === 1) return source;
  if (markerCount === 0 && anchors.length === 1) {
    const indentation = anchors[0][1];
    const method = HEXAGON_MESH_METHOD.replaceAll("\t", indentation);
    return source.replace(
      MESH_ANCHOR,
      `${method}\n${indentation}static get instanceSize() {`,
    );
  }
  throw new Error(
    `Unexpected PlayCanvas GSplat mesh state: marker=${markerCount}, anchor=${anchors.length}`,
  );
};

export const patchGsplatHybridMeshSource = (source) => {
  const originalCount = source.split(ORIGINAL_HYBRID_MESH).length - 1;
  const patchedCount = source.split(HEXAGON_HYBRID_MESH).length - 1;
  if (originalCount === 2 && patchedCount === 0) {
    return source.replaceAll(ORIGINAL_HYBRID_MESH, HEXAGON_HYBRID_MESH);
  }
  if (originalCount === 0 && patchedCount === 2) return source;
  throw new Error(
    `Unexpected PlayCanvas hybrid mesh state: original=${originalCount}, patched=${patchedCount}`,
  );
};

export const patchGsplatHexagonIndexCountSource = (source) => {
  const originalCount = source.split(ORIGINAL_INDEX_COUNT).length - 1;
  const patchedCount = source.split(HEXAGON_INDEX_COUNT).length - 1;
  if (originalCount === 1 && patchedCount === 0) {
    return source.replace(ORIGINAL_INDEX_COUNT, HEXAGON_INDEX_COUNT);
  }
  if (originalCount === 0 && patchedCount === 1) return source;
  throw new Error(
    `Unexpected PlayCanvas GSplat index count state: original=${originalCount}, patched=${patchedCount}`,
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

  let changed = 0;
  for (const build of ["playcanvas", "playcanvas.dbg", "playcanvas.prf"]) {
    const meshPath = resolve(
      packageRoot,
      "build",
      build,
      "src/scene/gsplat/gsplat-resource-base.js",
    );
    const meshSource = await readFile(meshPath, "utf8");
    const patchedMesh = patchGsplatHexagonMeshSource(meshSource);
    if (patchedMesh !== meshSource) {
      await writeFile(meshPath, patchedMesh, "utf8");
      changed += 1;
    }
    const hybridPath = resolve(
      packageRoot,
      "build",
      build,
      "src/scene/gsplat-unified/gsplat-hybrid-renderer.js",
    );
    const hybridSource = await readFile(hybridPath, "utf8");
    const patchedHybrid = patchGsplatHybridMeshSource(hybridSource);
    if (patchedHybrid !== hybridSource) {
      await writeFile(hybridPath, patchedHybrid, "utf8");
      changed += 1;
    }
    const projectorPath = resolve(
      packageRoot,
      "build",
      build,
      "src/scene/gsplat-unified/gsplat-projector.js",
    );
    const projectorSource = await readFile(projectorPath, "utf8");
    const patchedProjector =
      patchGsplatHexagonIndexCountSource(projectorSource);
    if (patchedProjector !== projectorSource) {
      await writeFile(projectorPath, patchedProjector, "utf8");
      changed += 1;
    }
  }
  process.stdout.write(
    `PlayCanvas ${PLAYCANVAS_VERSION} hexagon footprint patch: ${changed} artifact(s) changed\n`,
  );
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await patchInstalledPlayCanvas();
}
