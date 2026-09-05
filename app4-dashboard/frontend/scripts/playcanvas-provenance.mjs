import { createHash } from "node:crypto";
import { readFile, readdir, writeFile, mkdir } from "node:fs/promises";
import { join, relative } from "node:path";

const root = "node_modules/playcanvas";
const files = {};
async function walk(directory) {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await walk(path);
    else if (entry.isFile()) files[relative(root, path)] = createHash("sha256").update(await readFile(path)).digest("hex");
  }
}
await walk(join(root, "build"));
const { version } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
await mkdir("public", { recursive: true });
await writeFile("public/playcanvas-provenance.json", JSON.stringify({ version, algorithm: "sha256", files }, null, 2) + "\n");
