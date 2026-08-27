import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [evidenceArg, repoArg, outputArg] = process.argv.slice(2);
assert(evidenceArg && repoArg && outputArg, 'Usage: node replay-budget.mjs EVIDENCE_ROOT REPOSITORY_ROOT NEW_OUTPUT_JSON (Node 24+)');
const evidence = resolve(evidenceArg), repository = resolve(repoArg), MiB = 1024 ** 2;
const hash = data => createHash('sha256').update(data).digest('hex');
const priorAudit = JSON.parse(await readFile(resolve(repository, 'docs/benchmarks/gstile-prefetch-freshness-replay.json')));
const inputHashes = {};
async function read(relative) {
  const data = await readFile(resolve(evidence, relative));
  inputHashes[relative] = hash(data);
  assert.equal(inputHashes[relative], priorAudit.inputHashes[relative], `Cohort fingerprint changed: ${relative}`);
  return data.toString('utf8');
}
const sourceRoot = resolve(repository, 'app4-dashboard/frontend/app/lib/gstile');
const {selectGsTileLod} = await import(pathToFileURL(resolve(sourceRoot, 'lod-selection.ts')));
const {gstileAdaptivePrefetchBudget, MINIMUM_GSTILE_PREFETCH_BYTES, DEFAULT_GSTILE_PREFETCH_BYTES,
  GSTILE_PREFETCH_UTILITY_SAMPLE_BYTES, GSTILE_PREFETCH_TARGET_UTILITY,
  gstilePrefetchProjection, planGsTilePrefetchPacks} = await import(pathToFileURL(resolve(sourceRoot, 'lod-prefetch.ts')));
const protocol = JSON.parse(await read('protocol.json'));
assert.equal(protocol.schema, 'gstile-cache-camera-pilot-v1');
const {manifest} = JSON.parse(await read('descriptor-original.json'));
const requests = (await read('network.jsonl')).trim().split('\n').map(JSON.parse);
const packs = new Map(manifest.packs.map(pack => [pack.id, pack]));
const nodes = new Map(manifest.nodes.map(node => [node.id, node]));
const sum = (values, fn) => [...values].reduce((total, value) => total + fn(value), 0);
const decodedBytes = ids => sum(ids, id => packs.get(id).byteLength);
const wireBytes = ids => sum(ids, id => packs.get(id).encodings.zstd.byteLength);
const packId = request => {
  assert(request.complete);
  const match = /^\/data\/([^/]+)\/zstd$/.exec(request.path);
  assert(match && packs.has(match[1]));
  assert.equal(request.bytes, packs.get(match[1]).encodings.zstd.byteLength);
  return match[1];
};
function budget(completed, useful, floor) {
  const maximumBytes = completed < GSTILE_PREFETCH_UTILITY_SAMPLE_BYTES ? DEFAULT_GSTILE_PREFETCH_BYTES :
    Math.round(Math.max(floor, Math.min(DEFAULT_GSTILE_PREFETCH_BYTES,
      DEFAULT_GSTILE_PREFETCH_BYTES * (useful / completed) / GSTILE_PREFETCH_TARGET_UTILITY)) / MiB) * MiB;
  if (floor === MINIMUM_GSTILE_PREFETCH_BYTES) {
    assert.equal(maximumBytes, gstileAdaptivePrefetchBudget(completed, useful).maximumBytes);
  }
  return maximumBytes;
}
const rows = [];
const workingSets = [];
for (const spec of protocol.runs.filter(run => run.arm === 'candidate')) {
  const run = JSON.parse(await read(`results/${spec.label}.json`));
  assert(run.complete && !run.failure && run.errors.length === 0);
  assert.equal(run.bundleId, manifest.bundleId);
  const log = requests.filter(request => request.run === spec.label);
  const initialAvailable = new Set(log.slice(0, run.phases[0].networkBefore.zstdResponses).map(packId));
  const representedPacks = snapshot => new Set(snapshot.selection.selectedNodeIds.map(id => {
    const node = nodes.get(id); return (node.tile ?? node.lodTile).pack;
  }));
  const door = representedPacks(run.doorStart), facade = representedPacks(run.phases[0].snapshot);
  const union = new Set([...door, ...facade]);
  workingSets.push({label: spec.label, doorBytes: decodedBytes(door), facadeBytes: decodedBytes(facade),
    unionPacks: union.size, unionBytes: decodedBytes(union),
    capacityMarginsMiB: [768, 1024, 1536, 2048].map(capacityMiB => ({capacityMiB,
      freeAfterUnionMiB: capacityMiB - decodedBytes(union) / MiB}))});
  for (const floorMiB of [96, 64, 32]) {
    const available = new Set(initialAvailable), newlyPrefetched = new Set();
    let completed = run.phases[0].networkBefore.prefetchCompletedBytes;
    const useful = run.phases[0].networkBefore.prefetchUsefulBytes;
    const phases = [];
    for (const phase of run.phases) {
      assert.equal(phase.commitCount, 1);
      assert.equal(phase.networkDelta.prefetchUsefulBytes, 0);
      assert.equal(phase.networkDelta.prefetchCancellations, 0);
      assert.equal(phase.networkDelta.prefetchPersistentBytes, 0);
      assert.equal(phase.networkDelta.networkRetries, 0);
      assert.equal(phase.networkDelta.zstdFallbacks, 0);
      assert.equal(phase.snapshot.performance.lodPrefetch.errors, 0);
      assert(phase.readyAt - phase.inputEnd >= 1500, 'Fresh motion needs trajectory prediction, outside this replay');
      // This dataset stays below the persistent ceiling before every demand
      // phase. Stop if an earlier modeled prefetch would require an eviction.
      assert(decodedBytes(available) <= protocol.persistentCacheBytes, 'Cannot model subsequent demand after cache eviction');
      const camera = phase.snapshot.benchmarkCamera;
      const options = {
        cameraPosition: camera.position.map((v, i) => v + manifest.coordinateFrame.origin[i]),
        cameraDirection: [-camera.view[2], -camera.view[6], -camera.view[10]],
        cameraUp: [camera.view[1], camera.view[5], camera.view[9]],
        verticalFovRadians: camera.fov * Math.PI / 180,
        viewportWidth: camera.viewport[0], viewportHeight: camera.viewport[1],
        maximumResidentGaussians: 7_500_000, maximumProjectedErrorPixels: 2,
        includeSiblingLeaves: false, retainOffscreenCoverage: true,
      };
      const visible = selectGsTileLod(manifest, options);
      assert.deepEqual([...visible.selectedNodeIds].sort(), [...phase.snapshot.selection.selectedNodeIds].sort());
      assert.equal(visible.residentGaussians, phase.snapshot.resident.gaussianCount);
      const demand = new Set(visible.selectedNodeIds.map(id => {
        const node = nodes.get(id); return (node.tile ?? node.lodTile).pack;
      }));
      // Existing useful telemetry only credits RAM hits. For this cohort there
      // are no new prefetch/next-cut intersections at all, so RAM residency and
      // IndexedDB attribution cannot change the modeled utility numerator.
      assert([...demand].every(id => !newlyPrefetched.has(id)), 'New useful prefetch requires RAM admission/hit tracing');
      const misses = new Set([...demand].filter(id => !available.has(id)));
      const observedDemand = log.slice(phase.networkBefore.zstdResponses,
        phase.networkAfter.zstdResponses - phase.snapshot.performance.lodPrefetch.plannedNodes).map(packId);
      assert.deepEqual([...misses].sort(), [...new Set(observedDemand)].sort(), 'Demand traffic changed');
      for (const id of demand) available.add(id);
      assert(decodedBytes(available) <= protocol.persistentCacheBytes);
      const projection = gstilePrefetchProjection(options.verticalFovRadians,
        options.viewportWidth, options.viewportHeight, 1.75, 80 * Math.PI / 180);
      const expanded = selectGsTileLod(manifest, {...options, ...projection,
        maximumResidentGaussians: 30_000_000, retainOffscreenCoverage: false});
      const maximumBytes = budget(completed, useful, floorMiB * MiB);
      const planned = planGsTilePrefetchPacks(manifest, visible.selectedNodeIds,
        expanded.selectedNodeIds, maximumBytes, available);
      const ids = new Set(planned.map(entry => entry.pack.id));
      const bytes = decodedBytes(ids);
      phases.push({name: phase.name, visibleGaussians: visible.residentGaussians,
        demandPacks: misses.size, demandCompressedBytes: wireBytes(misses),
        completedBefore: completed, usefulBefore: useful, utilityRatio: useful / completed,
        budgetBytes: maximumBytes, prefetchPacks: ids.size,
        prefetchDecodedBytes: bytes, prefetchCompressedBytes: wireBytes(ids),
        prefetchPackIds: [...ids], availableBeforePrefetchBytes: decodedBytes(available)});
      completed += bytes;
      for (const id of ids) { available.add(id); newlyPrefetched.add(id); }
    }
    rows.push({label: spec.label, floorMiB, initialCacheBytes: decodedBytes(initialAvailable),
      prefetchCompressedBytes: sum(phases, p => p.prefetchCompressedBytes),
      demandCompressedBytes: sum(phases, p => p.demandCompressedBytes),
      cumulativeUniqueBytesAtEnd: decodedBytes(available),
      terminalEvictionNotModeled: decodedBytes(available) > protocol.persistentCacheBytes,
      phases});
  }
}
const sources = {};
for (const name of ['lod-prefetch.ts', 'lod-selection.ts', 'playcanvas-backend.ts']) {
  sources[name] = hash(await readFile(resolve(sourceRoot, name)));
}
const comparisons = protocol.runs.filter(run => run.arm === 'candidate').map(({label}) => {
  const reference = rows.find(row => row.label === label && row.floorMiB === 96);
  const candidate = rows.find(row => row.label === label && row.floorMiB === 64);
  const lower = rows.find(row => row.label === label && row.floorMiB === 32);
  const changePercent = (before, after) => (after / before - 1) * 100;
  const sameLowerPlan = JSON.stringify(candidate.phases) === JSON.stringify(lower.phases);
  assert(sameLowerPlan, 'Re-evaluate the chosen floor if the smaller floor changes the replay');
  assert(candidate.prefetchCompressedBytes < reference.prefetchCompressedBytes);
  assert.equal(candidate.demandCompressedBytes, reference.demandCompressedBytes);
  return {label, prefetchChangePercent: changePercent(reference.prefetchCompressedBytes, candidate.prefetchCompressedBytes),
    totalPhaseTrafficChangePercent: changePercent(reference.prefetchCompressedBytes + reference.demandCompressedBytes,
      candidate.prefetchCompressedBytes + candidate.demandCompressedBytes),
    revisitTrafficChangePercent: changePercent(reference.phases[2].prefetchCompressedBytes, candidate.phases[2].prefetchCompressedBytes),
    samePlanAt32MiB: sameLowerPlan};
});
const report = {schema: 'gstile-prefetch-budget-sequential-v1', node: process.version,
  scope: 'Conditional sequential replay from recorded warmed door state, not a cold-start, latency, GPU or OS memory benchmark. Freshness correction enabled in all policies. Recomputed availability and cumulative completed-byte feedback; reject demand/utility or pre-demand eviction ambiguity.',
  bundleId: manifest.bundleId, scriptSha256: hash(await readFile(new URL(import.meta.url))),
  sources, inputHashes, productionFloorBytes: MINIMUM_GSTILE_PREFETCH_BYTES, comparisons, workingSets, rows};
await writeFile(resolve(outputArg), JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify({comparisons, plans: rows.map(row => ({label: row.label,
  floorMiB: row.floorMiB, prefetchCompressedBytes: row.prefetchCompressedBytes,
  demandCompressedBytes: row.demandCompressedBytes,
  budgetsMiB: row.phases.map(p => p.budgetBytes / MiB),
  requests: row.phases.map(p => p.prefetchPacks)}))}, null, 2));
