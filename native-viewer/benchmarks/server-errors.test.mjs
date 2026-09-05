import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

for (const [name, port] of [['serve-webgpu.mjs', 8768], ['serve-streaming.mjs', 8770]]) {
  test(`${name} retains error details only in server logs`, { timeout: 15000 }, async () => {
    const root = await mkdtemp(join(tmpdir(), 'gstile-http-errors-'));
    const bundle = join(root, 'private-bundle');
    await mkdir(bundle);
    await writeFile(join(bundle, 'manifest.json'), JSON.stringify({ packs: [] }));
    await writeFile(join(root, 'fixture.txt'), 'fixture');
    const child = spawn(process.execPath, [fileURLToPath(new URL(name, import.meta.url)),
      root, bundle, join(root, 'private-camera.json'), join(root, 'report.json')],
      { stdio: ['ignore', 'pipe', 'pipe'] });
    let logs = '';
    child.stderr.on('data', chunk => { logs += chunk; });
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Benchmark server did not start')), 5000);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(new Error(`Server exited: ${code}`)); });
        child.stdout.on('data', chunk => {
          if (String(chunk).includes('GSTile comparison:')) { clearTimeout(timer); resolve(); }
        });
      });
      for (const [path, init] of [
        ['/native.json', {}],
        ['/fixture.txt', { headers: { Range: 'invalid-range' } }],
        ['/results', { method: 'POST', body: 'private-invalid-json' }],
      ]) {
        const response = await fetch(`http://127.0.0.1:${port}${path}`, init);
        assert.equal(response.status, 500);
        assert.match(response.headers.get('content-type'), /^text\/plain/);
        assert.equal(await response.text(), 'Internal server error');
      }
    } finally {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
    assert.match(logs, /Request failed/);
    assert.match(logs, /private-camera\.json/);
    assert.match(logs, /Invalid range/);
  });
}
