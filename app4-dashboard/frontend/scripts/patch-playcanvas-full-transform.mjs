import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLAYCANVAS_VERSION = "2.21.4";
const VARIANTS = ["playcanvas"];

const READ_ORIGINAL = `var<private> cachedTransformA: vec4u;
var<private> cachedTransformB: vec2u;
var<private> cachedColor: vec4f;`;
const READ_PATCHED = `var<private> cachedTransformA: vec4u;
var<private> cachedTransformB: vec4u;
var<private> cachedTransformC: vec4u;
var<private> cachedColor: vec4f;`;

const CENTER_ORIGINAL = `cachedTransformA = loadDataTransformA();
\tcachedTransformB = loadDataTransformB().xy;
\treturn vec3f(bitcast<f32>(cachedTransformA.r), bitcast<f32>(cachedTransformA.g), bitcast<f32>(cachedTransformA.b));`;
const CENTER_PATCHED = `cachedTransformA = loadDataTransformA();
\tcachedTransformB = loadDataTransformB();
\tcachedTransformC = loadDataTransformC();
\treturn vec3f(bitcast<f32>(cachedTransformA.r), bitcast<f32>(cachedTransformA.g), bitcast<f32>(cachedTransformA.b));`;

const ROTATION_ORIGINAL = `let rotXY = unpack2x16float(cachedTransformA.a);
\tlet rotZscaleX = unpack2x16float(cachedTransformB.x);
\tlet rotXYZ = vec3f(rotXY, rotZscaleX.x);
\treturn vec4f(rotXYZ, sqrt(max(0.0, 1.0 - dot(rotXYZ, rotXYZ)))).wxyz;`;
const ROTATION_PATCHED = `let rotXYZ = vec3f(
\t\tbitcast<f32>(cachedTransformA.a),
\t\tbitcast<f32>(cachedTransformB.x),
\t\tbitcast<f32>(cachedTransformB.y)
\t);
\treturn vec4f(rotXYZ, bitcast<f32>(cachedTransformC.y)).wxyz;`;

const SCALE_ORIGINAL = `let rotZscaleX = unpack2x16float(cachedTransformB.x);
\tlet scaleYZ = unpack2x16float(cachedTransformB.y);
\treturn vec3f(rotZscaleX.y, scaleYZ);`;
const SCALE_PATCHED = `return vec3f(
\t\tbitcast<f32>(cachedTransformB.z),
\t\tbitcast<f32>(cachedTransformB.w),
\t\tbitcast<f32>(cachedTransformC.x)
\t);`;

const WRITE_ORIGINAL = `writeDataTransformA(vec4u(bitcast<u32>(center.x), bitcast<u32>(center.y), bitcast<u32>(center.z), pack2x16float(rotation.xy)));
\t\twriteDataTransformB(vec4u(pack2x16float(vec2f(rotation.z, scale.x)), pack2x16float(scale.yz), 0u, 0u));`;
const WRITE_PATCHED = `writeDataTransformA(vec4u(
\t\t\tbitcast<u32>(center.x), bitcast<u32>(center.y), bitcast<u32>(center.z), bitcast<u32>(rotation.x)
\t\t));
\t\twriteDataTransformB(vec4u(
\t\t\tbitcast<u32>(rotation.y), bitcast<u32>(rotation.z), bitcast<u32>(scale.x), bitcast<u32>(scale.y)
\t\t));
\t\twriteDataTransformC(vec4u(
\t\t\tbitcast<u32>(scale.z), bitcast<u32>(rotation.w), 0u, 0u
\t\t));`;

const STREAMS_ORIGINAL = `{ name: "dataTransformA", format: PIXELFORMAT_RGBA32U },
\t\t\t\t{ name: "dataTransformB", format: PIXELFORMAT_RG32U }`;
const STREAMS_PATCHED = `{ name: "dataTransformA", format: PIXELFORMAT_RGBA32U },
\t\t\t\t{ name: "dataTransformB", format: PIXELFORMAT_RGBA32U },
\t\t\t\t{ name: "dataTransformC", format: PIXELFORMAT_RGBA32U }`;

const replaceOnce = (source, original, patched, label) => {
  if (source.includes(patched)) return source;
  const count = source.split(original).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one source fragment, found ${count}`);
  return source.replace(original, patched);
};

export const patchFullTransformReadSource = (source) =>
  [
    [READ_ORIGINAL, READ_PATCHED, "read cache"],
    [CENTER_ORIGINAL, CENTER_PATCHED, "center read"],
    [ROTATION_ORIGINAL, ROTATION_PATCHED, "rotation read"],
    [SCALE_ORIGINAL, SCALE_PATCHED, "scale read"],
  ].reduce(
    (value, [original, replacement, label]) =>
      replaceOnce(value, original, replacement, label),
    source,
  );

export const patchFullTransformWriteSource = (source) =>
  replaceOnce(source, WRITE_ORIGINAL, WRITE_PATCHED, "transform write");

export const patchFullTransformStreamsSource = (source) =>
  replaceOnce(source, STREAMS_ORIGINAL, STREAMS_PATCHED, "transform streams");

const patchFile = async (path, patcher) => {
  const source = await readFile(path, "utf8");
  const patched = patcher(source);
  if (patched !== source) await writeFile(path, patched, "utf8");
  return patched !== source;
};

const patchInstalledPlayCanvas = async () => {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const packageRoot = resolve(frontendRoot, "node_modules/playcanvas");
  const packageJson = JSON.parse(await readFile(resolve(packageRoot, "package.json"), "utf8"));
  if (packageJson.version !== PLAYCANVAS_VERSION) {
    throw new Error(`PlayCanvas ${packageJson.version} is not the audited ${PLAYCANVAS_VERSION} release`);
  }

  let changed = 0;
  for (const variant of VARIANTS) {
    const root = resolve(packageRoot, "build", variant, "src");
    changed += Number(await patchFile(
      resolve(root, "scene/shader-lib/wgsl/chunks/gsplat/vert/formats/containerPackedRead.js"),
      patchFullTransformReadSource,
    ));
    changed += Number(await patchFile(
      resolve(root, "scene/shader-lib/wgsl/chunks/gsplat/frag/formats/containerPackedWrite.js"),
      patchFullTransformWriteSource,
    ));
    changed += Number(await patchFile(
      resolve(root, "scene/gsplat-unified/gsplat-params.js"),
      patchFullTransformStreamsSource,
    ));
  }
  process.stdout.write(`PlayCanvas ${PLAYCANVAS_VERSION} float32 work-buffer patch: ${changed} files changed\n`);
};

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) await patchInstalledPlayCanvas();
