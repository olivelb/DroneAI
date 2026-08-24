import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const FULL_REBUILD_PATTERN =
  /^[ \t]*if \(this\._placementSetChanged\) \{\r?\n[ \t]*newState\.fullRebuild = true;\r?\n[ \t]*\}/gm;
const INCREMENTAL_MARKER =
  "Placement churn is handled by GSplatWorldState's allocation diff.";
const FULL_REBUILD_PATCHED = `\t\t// ${INCREMENTAL_MARKER}
\t\t// Existing allocations remain valid; only new or moved blocks need upload.`;

const PLAYCANVAS_BUILD_FILES = [
  "build/playcanvas/src/scene/gsplat-unified/gsplat-world.js",
  "build/playcanvas.dbg/src/scene/gsplat-unified/gsplat-world.js",
  "build/playcanvas.prf/src/scene/gsplat-unified/gsplat-world.js",
];

export const patchPlayCanvasIncrementalWorldSource = (source) => {
  const matches = [...source.matchAll(FULL_REBUILD_PATTERN)];
  const originalCount = matches.length;
  const patchedCount = source.split(INCREMENTAL_MARKER).length - 1;
  if (originalCount === 1 && patchedCount === 0) {
    const indentation = matches[0][0].match(/^[ \t]*/)?.[0] ?? "";
    return source.replace(
      FULL_REBUILD_PATTERN,
      FULL_REBUILD_PATCHED.replaceAll("\t\t", indentation),
    );
  }
  if (originalCount === 0 && patchedCount === 1) return source;
  throw new Error(
    `Unexpected PlayCanvas placement rebuild state: original=${originalCount}, patched=${patchedCount}`,
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
  for (const relativePath of PLAYCANVAS_BUILD_FILES) {
    const path = resolve(packageRoot, relativePath);
    const source = await readFile(path, "utf8");
    const patched = patchPlayCanvasIncrementalWorldSource(source);
    if (patched !== source) {
      await writeFile(path, patched, "utf8");
      changed += 1;
    }
  }
  process.stdout.write(
    changed === 0
      ? `PlayCanvas ${PLAYCANVAS_VERSION} incremental GSplat world patch already applied\n`
      : `Patched ${changed} PlayCanvas ${PLAYCANVAS_VERSION} GSplat world artifacts\n`,
  );
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await patchInstalledPlayCanvas();
}
