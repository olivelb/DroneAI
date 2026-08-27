import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [evidenceArg, repoArg, outputArg] = process.argv.slice(2);
assert(evidenceArg && repoArg && outputArg, 'Usage: node replay-prefetch.mjs EVIDENCE_ROOT REPOSITORY_ROOT NEW_OUTPUT_JSON (Node >= 24)');
const evidence = resolve(evidenceArg), repository = resolve(repoArg);
const inputHashes = {};
const hash = data => createHash('sha256').update(data).digest('hex');
async function read(relative) {
  const data = await readFile(resolve(evidence, relative));
  inputHashes[relative] = hash(data);
  return data.toString('utf8');
}
const sourceRoot = resolve(repository, 'app4-dashboard/frontend/app/lib/gstile');
const sources = {};
for (const name of ['lod-prefetch.ts', 'lod-selection.ts', 'playcanvas-backend.ts']) {
  sources[name] = hash(await readFile(resolve(sourceRoot, name)));
}
const {selectGsTileLod} = await import(pathToFileURL(resolve(sourceRoot, 'lod-selection.ts')));
const {gstilePrefetchProjection, planGsTilePrefetchPacks, predictGsTileCameraPose} =
  await import(pathToFileURL(resolve(sourceRoot, 'lod-prefetch.ts')));
const protocol = JSON.parse(await read('protocol.json'));
assert.equal(protocol.schema, 'gstile-cache-camera-pilot-v1');
const {manifest} = JSON.parse(await read('descriptor-original.json'));
const requests = (await read('network.jsonl')).trim().split('\n').map(JSON.parse);
const packs = new Map(manifest.packs.map(pack => [pack.id, pack]));
const nodes = new Map(manifest.nodes.map(node => [node.id, node]));
const packId = request => {
  assert(request.complete, 'Incomplete transfer cannot reconstruct availability');
  const match = /^\/data\/([^/]+)\/zstd$/.exec(request.path);
  assert(match && packs.has(match[1]), 'Unexpected transport or pack');
  assert.equal(request.bytes, packs.get(match[1]).encodings.zstd.byteLength);
  return match[1];
};
const sum = (values, fn) => [...values].reduce((total, value) => total + fn(value), 0);
const sorted = ids => [...ids].sort();
const rows = [];
for (const spec of protocol.runs.filter(run => run.arm === 'candidate')) {
  const run = JSON.parse(await read(`results/${spec.label}.json`));
  assert(run.complete && !run.failure && run.errors.length === 0);
  assert.equal(run.bundleId, manifest.bundleId);
  const log = requests.filter(request => request.run === spec.label);
  for (const [index, phase] of run.phases.entries()) {
    const {snapshot, networkBefore: before, networkAfter: after, networkDelta: delta} = phase;
    const observed = snapshot.performance.lodPrefetch;
    assert.equal(phase.commitCount, 1, 'This replay requires one settled commit per phase');
    assert.equal(observed.errors, 0);
    assert.equal(observed.plannedNodes, observed.completedNodes);
    assert.equal(observed.plannedNodes, delta.prefetchCompletedRequests);
    assert.equal(delta.prefetchPersistentBytes, 0);
    assert.equal(delta.prefetchCancellations, 0);
    assert.equal(delta.networkRetries, 0);
    assert.equal(delta.zstdFallbacks, 0);
    // Demand is finished before this one post-commit prefetch batch starts.
    const prefetchStart = after.zstdResponses - observed.plannedNodes;
    assert(prefetchStart >= before.zstdResponses);
    const speculative = log.slice(prefetchStart, after.zstdResponses);
    const oldIds = new Set(speculative.map(packId));
    assert.equal(oldIds.size, observed.plannedNodes);
    assert.equal(sum(speculative, r => r.bytes), delta.prefetchNetworkBytes);
    assert.equal(sum(oldIds, id => packs.get(id).byteLength), observed.plannedBytes);
    const available = new Set(log.slice(0, prefetchStart).map(packId));
    assert.equal(available.size, observed.locallyAvailablePacks, 'Availability reconstruction differs');
    assert.equal(sum(available, id => packs.get(id).byteLength), observed.locallyAvailableBytes);
    assert(observed.locallyAvailableBytes <= protocol.persistentCacheBytes, 'Cache eviction needs a full trace');
    const phaseRequests = log.slice(before.zstdResponses, after.zstdResponses);
    assert.equal(sum(phaseRequests, r => r.bytes), delta.networkBytes);
    const previouslyRequested = new Set(log.slice(0, before.zstdResponses).map(packId));
    const repeated = phaseRequests.filter(r => previouslyRequested.has(packId(r)));
    const camera = snapshot.benchmarkCamera;
    const pose = {
      position: camera.position,
      direction: [-camera.view[2], -camera.view[6], -camera.view[10]],
      up: [camera.view[1], camera.view[5], camera.view[9]],
    };
    const options = {
      cameraPosition: pose.position.map((value, axis) => value + manifest.coordinateFrame.origin[axis]),
      cameraDirection: pose.direction, cameraUp: pose.up,
      verticalFovRadians: camera.fov * Math.PI / 180,
      viewportWidth: camera.viewport[0], viewportHeight: camera.viewport[1],
      maximumResidentGaussians: 7_500_000, maximumProjectedErrorPixels: 2,
      includeSiblingLeaves: false, retainOffscreenCoverage: true,
    };
    const visible = selectGsTileLod(manifest, options);
    assert.deepEqual(sorted(visible.selectedNodeIds), sorted(snapshot.selection.selectedNodeIds));
    assert.equal(visible.residentGaussians, snapshot.resident.gaussianCount);
    // Last recorded sample precedes inputEnd; commit precedes timer scheduling.
    // Even without the nominal 600 ms timer, every recorded estimate is stale.
    const ageLowerBoundMs = phase.readyAt - phase.inputEnd;
    assert(ageLowerBoundMs >= 1_500);
    assert.equal(observed.predictionActive, true);
    const motion = {pose, timestampMs: phase.inputEnd, samples: observed.motionSamples,
      positionVelocity: [observed.positionSpeed, 0, 0], directionVelocity: [0, 0, 0], upVelocity: [0, 0, 0]};
    // Direction of the velocity is irrelevant to this age-only rejection.
    assert.equal(predictGsTileCameraPose(motion, 1_500, 10, Math.PI / 4,
      phase.inputEnd + ageLowerBoundMs), null);
    const expanded = selectGsTileLod(manifest, {...options,
      ...gstilePrefetchProjection(options.verticalFovRadians, options.viewportWidth, options.viewportHeight, 1.75, 80 * Math.PI / 180),
      maximumResidentGaussians: 30_000_000, retainOffscreenCoverage: false});
    const planned = planGsTilePrefetchPacks(manifest, visible.selectedNodeIds,
      expanded.selectedNodeIds, observed.budgetBytes, available);
    const newIds = new Set(planned.map(entry => entry.pack.id));
    const nextPhase = run.phases[index + 1];
    const nextDemand = nextPhase ? new Set(nextPhase.snapshot.selection.selectedNodeIds.map(id => {
      const node = nodes.get(id); return (node.tile ?? node.lodTile).pack;
    })) : null;
    rows.push({label: spec.label, phase: phase.name, ageLowerBoundMs,
      nominalAgeAfterTimerMs: ageLowerBoundMs + 600,
      visibleCutMatches: true, visibleGaussians: visible.residentGaussians,
      repeatedNetworkRequests: repeated.length, availablePacks: available.size,
      budgetBytes: observed.budgetBytes,
      observed: {packs: oldIds.size, decodedBytes: observed.plannedBytes, compressedBytes: delta.prefetchNetworkBytes,
        predictedPacks: observed.predictedNodes},
      stationaryPlan: {packs: newIds.size, decodedBytes: sum(planned, e => e.pack.byteLength),
        compressedBytes: sum(planned, e => e.pack.encodings.zstd.byteLength),
        packIds: [...newIds], overlapWithObserved: sum(newIds, id => Number(oldIds.has(id)))},
      nextVisibleDemand: nextDemand ? {
        observedPrefetchPackHits: sum(oldIds, id => Number(nextDemand.has(id))),
        stationaryPrefetchPackHits: sum(newIds, id => Number(nextDemand.has(id))),
      } : null});
  }
}
const report = {schema: 'gstile-prefetch-freshness-offline-v1', node: process.version,
  scope: 'One-step counterfactual at fixed recorded availability and budget; not an end-to-end latency or whole-path traffic benchmark. No GPU execution.',
  bundleId: manifest.bundleId, scriptSha256: hash(await readFile(new URL(import.meta.url))), sources, inputHashes, rows};
await writeFile(resolve(outputArg), JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify(rows.map(row => ({...row,
  stationaryPlan: {...row.stationaryPlan, packIds: undefined}})), null, 2));
