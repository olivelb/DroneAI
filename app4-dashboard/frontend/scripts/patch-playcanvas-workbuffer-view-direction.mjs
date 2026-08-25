import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const SHADER_MARKER = "DroneAI shared work-buffer view direction";
const PASS_MARKER = "DroneAI stable work-buffer pass names";

const replaceExactlyOnce = (source, original, replacement, label) => {
  const originalCount = source.split(original).length - 1;
  const replacementCount = source.split(replacement).length - 1;
  if (originalCount === 1 && replacementCount === 0) {
    return source.replace(original, replacement);
  }
  if (originalCount === 0 && replacementCount === 1) return source;
  throw new Error(
    `Unexpected PlayCanvas ${label} state: original=${originalCount}, patched=${replacementCount}`,
  );
};

export const patchGsplatWorkBufferShaderSource = (source, language) => {
  if (source.includes(SHADER_MARKER)) {
    const patchedConditions =
      source.split(
        "#if SH_BANDS > 0 || defined(DRONEGS_WORKBUFFER_VIEW_DIRECTION)",
      ).length - 1;
    const assignments =
      source.split("droneWorkBufferViewDirection = dir;").length - 1;
    if (patchedConditions === 3 && assignments === 1) return source;
    throw new Error(
      `Incomplete PlayCanvas ${language} shared direction patch: conditions=${patchedConditions}, assignments=${assignments}`,
    );
  }
  const wgsl = language === "wgsl";
  const include = '#include "gsplatModifyVS"';
  const helper = wgsl
    ? `// ${SHADER_MARKER}\nvar<private> droneWorkBufferViewDirection: vec3f;\nfn getDroneWorkBufferViewDirection() -> vec3f {\n    return droneWorkBufferViewDirection;\n}\n${include}`
    : `// ${SHADER_MARKER}\nvec3 droneWorkBufferViewDirection;\nvec3 getDroneWorkBufferViewDirection() {\n    return droneWorkBufferViewDirection;\n}\n${include}`;
  let result = replaceExactlyOnce(
    source,
    include,
    helper,
    `${language} direction helper`,
  );
  const condition = "#if SH_BANDS > 0";
  const conditionCount = result.split(condition).length - 1;
  if (conditionCount !== 4) {
    throw new Error(
      `Unexpected PlayCanvas ${language} SH condition count: ${conditionCount}`,
    );
  }
  let replacedConditions = 0;
  result = result.replaceAll(condition, (match) => {
    replacedConditions += 1;
    return replacedConditions <= 3
      ? `${match} || defined(DRONEGS_WORKBUFFER_VIEW_DIRECTION)`
      : match;
  });
  const colorPattern = wgsl
    ? /\n([ \t]+)(var color = getColor\(\);)/g
    : /\n([ \t]+)(vec4 color = getColor\(\);)/g;
  const colorMatches = [...result.matchAll(colorPattern)];
  if (colorMatches.length !== 1) {
    throw new Error(
      `Unexpected PlayCanvas ${language} color anchor count: ${colorMatches.length}`,
    );
  }
  const indentation = colorMatches[0][1];
  return result.replace(
    colorPattern,
    `\n${indentation}#ifdef DRONEGS_WORKBUFFER_VIEW_DIRECTION\n` +
      `${indentation}    droneWorkBufferViewDirection = dir;\n` +
      `${indentation}#endif\n${indentation}$2`,
  );
};

export const patchGsplatWorkBufferPassSource = (source) => {
  if (source.includes(PASS_MARKER)) return source;
  const pattern = /\n([ \t]+)(this\.colorOnly = colorOnly;)/g;
  const matches = [...source.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(
      `Unexpected PlayCanvas work-buffer pass name anchor count: ${matches.length}`,
    );
  }
  const indentation = matches[0][1];
  return source.replace(
    pattern,
    `\n${indentation}$2\n` +
      `${indentation}// ${PASS_MARKER}. Constructor names are minified in production.\n` +
      `${indentation}this.name = colorOnly ? "GSplatWorkBufferColor" : "GSplatWorkBufferFull";`,
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
    for (const language of ["glsl", "wgsl"]) {
      const path = resolve(
        packageRoot,
        "build",
        build,
        `src/scene/shader-lib/${language}/chunks/gsplat/frag/gsplatCopyToWorkbuffer.js`,
      );
      const source = await readFile(path, "utf8");
      const patched = patchGsplatWorkBufferShaderSource(source, language);
      if (patched !== source) {
        await writeFile(path, patched, "utf8");
        changed += 1;
      }
    }
    const passPath = resolve(
      packageRoot,
      "build",
      build,
      "src/scene/gsplat-unified/gsplat-work-buffer-render-pass.js",
    );
    const passSource = await readFile(passPath, "utf8");
    const patchedPass = patchGsplatWorkBufferPassSource(passSource);
    if (patchedPass !== passSource) {
      await writeFile(passPath, patchedPass, "utf8");
      changed += 1;
    }
  }
  process.stdout.write(
    `PlayCanvas ${PLAYCANVAS_VERSION} shared view-direction patch: ${changed} artifact(s) changed\n`,
  );
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await patchInstalledPlayCanvas();
}
