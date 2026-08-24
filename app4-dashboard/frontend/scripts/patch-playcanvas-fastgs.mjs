import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const ORIGINAL_JACOBIAN = "let J2 = -J1 / vz * v.xy;";
const PATCH_MARKER = "let projectedLimit = vec2f(";
const FASTGS_JACOBIAN = `let projectedLimit = vec2f(
            1.3 * viewportWidth / focal,
            1.3 * viewportHeight / focal
        );
        let projected = clamp(-v.xy / vz, -projectedLimit, projectedLimit);
        let J2 = J1 * projected;`;

const PLAYCANVAS_BUILD_FILES = [
  "build/playcanvas/src/scene/shader-lib/wgsl/chunks/gsplat/compute-gsplat-common.js",
  "build/playcanvas.dbg/src/scene/shader-lib/wgsl/chunks/gsplat/compute-gsplat-common.js",
  "build/playcanvas.prf/src/scene/shader-lib/wgsl/chunks/gsplat/compute-gsplat-common.js",
  "build/playcanvas.js",
  "build/playcanvas.dbg.js",
  "build/playcanvas.prf.js",
  "build/playcanvas.mjs",
  "build/playcanvas.dbg.mjs",
  "build/playcanvas.prf.mjs",
  "build/playcanvas.min.js",
  "build/playcanvas.min.mjs",
];

const countOccurrences = (source, fragment) =>
  source.split(fragment).length - 1;

export const fastGsProjectedRatio = ({
  coordinate,
  depth,
  focal,
  viewportDimension,
}) => {
  if (
    ![coordinate, depth, focal, viewportDimension].every(Number.isFinite) ||
    depth <= 0 ||
    focal <= 0 ||
    viewportDimension <= 0
  ) {
    throw new TypeError("FastGS projection inputs must be finite and positive");
  }
  const limit = (1.3 * viewportDimension) / focal;
  return Math.max(-limit, Math.min(limit, coordinate / depth));
};

export const patchFastGsProjectionSource = (source) => {
  const originalCount = countOccurrences(source, ORIGINAL_JACOBIAN);
  const patchedCount = countOccurrences(source, PATCH_MARKER);
  if (originalCount === 1 && patchedCount === 0) {
    return source.replace(ORIGINAL_JACOBIAN, FASTGS_JACOBIAN);
  }
  if (originalCount === 0 && patchedCount === 1) return source;
  throw new Error(
    `Unexpected PlayCanvas Jacobian state: original=${originalCount}, patched=${patchedCount}`,
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
    const patched = patchFastGsProjectionSource(source);
    if (patched !== source) {
      await writeFile(path, patched, "utf8");
      changed += 1;
    }
  }
  process.stdout.write(
    changed === 0
      ? `PlayCanvas ${PLAYCANVAS_VERSION} FastGS projection patch already applied\n`
      : `Patched ${changed} PlayCanvas ${PLAYCANVAS_VERSION} projection artifacts\n`,
  );
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await patchInstalledPlayCanvas();
}
