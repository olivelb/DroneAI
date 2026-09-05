// Fixed-cut alpha comparison against an explicitly recorded native camera.
import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(process.argv[2]);
const child = spawnSync(process.execPath, [here + '/prepare-webgpu.mjs', output], { stdio: 'inherit' });
if (child.status !== 0) throw Error('Transparency harness build failed');
await writeFile(output + '/webgpu.mjs', await readFile(here + '/transparency.mjs'));
