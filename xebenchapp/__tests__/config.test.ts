import {parseConfig} from '../src/harness/config';
import bundled from '../configs/cpu-reference-v2.json';
import v1 from '../configs/cpu-reference-v1.json';
import experiment from '../configs/experiment-charging-ab-v1.json';
import v3 from '../configs/cpu-reference-v3.json';

test('bundled CPU reference configuration is valid', () => {
  expect(parseConfig(bundled)).toEqual(bundled);
});

test('cpu-reference-v1 stays valid and sets no load settings', () => {
  expect(parseConfig(v1)).toEqual(v1);
  for (const key of ['useMmap', 'useMlock', 'noExtraBufts', 'flashAttnType']) {
    expect(parseConfig(v1).runs[0]).not.toHaveProperty(key);
  }
});

test('cpu-reference-v2 is cpu-reference-v1 with PocketPal\'s Android load defaults', () => {
  const {useMmap, useMlock, noExtraBufts, flashAttnType, ...run} = bundled.runs[0];
  expect({useMmap, useMlock, noExtraBufts, flashAttnType})
    .toEqual({useMmap: false, useMlock: false, noExtraBufts: false, flashAttnType: 'off'});
  expect({...bundled, id: v1.id, runs: [run]}).toEqual(v1);
});

test('cpu-reference-v3 runs Q4_K_M, Q4_0 and Q8_0 with v2 settings except flash attention auto and longer cooling', () => {
  expect(parseConfig(v3)).toEqual(v3);
  expect(v3.runs.map(run => [run.quant, run.modelFile])).toEqual([
    ['Q4_K_M', 'Llama-3.2-1B-Instruct-Q4_K_M.gguf'], ['Q4_0', 'Llama-3.2-1B-Instruct-Q4_0.gguf'],
    ['Q8_0', 'Llama-3.2-1B-Instruct-Q8_0.gguf']]);
  const {runs, id, ...rest} = v3;
  const {runs: v2Runs, id: v2Id, ...v2Rest} = bundled;
  expect([id, v2Id]).toEqual(['cpu-reference-v3', 'cpu-reference-v2']);
  // Nine repetitions share one battery-temperature baseline: a longer cooldown and readiness
  // wait let the phone cool back within the 2 °C bound instead of skipping a repetition.
  expect(rest).toEqual({...v2Rest, cooldownSec: 120, readinessTimeoutSec: 900});
  expect(rest.maxBatteryTempRiseC).toBe(2);
  const identity = ['id', 'modelFile', 'quant', 'modelSha256'];
  const others = (run: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(run).filter(([key]) => !identity.includes(key)));
  for (const run of runs) {
    expect(run.id).toMatch(/^llama32-q[0-9a-z]+-cpu$/);
    expect(run.modelSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(run.modelFile.endsWith(`-${run.quant}.gguf`)).toBe(true);
    expect(others(run)).toEqual({...others(v2Runs[0]), flashAttnType: 'auto'});
  }
});

test.each([
  {useMmap: 'false'}, {useMmap: 0}, {useMmap: null}, {useMlock: 'true'}, {noExtraBufts: 1},
  {flashAttnType: 'enabled'}, {flashAttnType: true}, {flashAttnType: null}, {flash_attn_type: 'off'},
])('rejects invalid load setting %j', change => {
  expect(() => parseConfig({...bundled, runs: [{...bundled.runs[0], ...change}]})).toThrow();
});

test('each load setting is kept exactly when set', () => {
  for (const change of [{useMmap: true}, {useMlock: true}, {noExtraBufts: true}, {flashAttnType: 'auto'},
    {flashAttnType: 'on'}]) {
    expect(parseConfig({...v1, runs: [{...v1.runs[0], ...change}]}).runs[0]).toEqual({...v1.runs[0], ...change});
  }
});

test.each([
  {schemaVersion: 2}, {nColdRuns: 0}, {nColdRuns: 2.5}, {cooldownSec: -1},
  {maxDecodeTokens: 0}, {runs: []}, {nGpuLayers: 99}, {readinessTimeoutSec: 5},
  {readinessTimeoutSec: 901}, {maxBatteryTempRiseC: 0.1}, {maxBatteryTempRiseC: 11}, {maxBatteryTempRiseC: '2'},
  {orderSeed: -1}, {orderSeed: 2 ** 32}, {orderSeed: 1.5},
  {experimentChargingReadiness: 'skip'}, {experimentChargingReadiness: true},
])('rejects invalid configuration %j', change => {
  expect(() => parseConfig({...bundled, ...change})).toThrow();
});

test.each([
  {engine: 'litert-lm'}, {modelFile: '../model.gguf'}, {modelFile: '/tmp/model.gguf'},
  {modelFile: 'https://example.test/model.gguf'}, {nThreads: 0}, {nCtx: 512}, {backend: 'gpu'},
  {modelSha256: 'A'.repeat(64)}, {modelSha256: 'a'.repeat(63)}, {modelSha256: null}, {modelSha256: 1},
])('rejects unsafe or unsupported run %j', change => {
  expect(() => parseConfig({...bundled, runs: [{...bundled.runs[0], ...change}]})).toThrow();
});

test('rejects duplicate run IDs', () => {
  expect(() => parseConfig({...bundled, runs: [bundled.runs[0], bundled.runs[0]]})).toThrow();
});

test('readiness timeout defaults to the protocol bound and a configured order seed is kept', () => {
  const legacy: Record<string, unknown> = {...bundled};
  delete legacy.readinessTimeoutSec;
  delete legacy.maxBatteryTempRiseC;
  expect(parseConfig(legacy).readinessTimeoutSec).toBe(180);
  expect(parseConfig(legacy).maxBatteryTempRiseC).toBe(2);
  expect(parseConfig({...bundled, maxBatteryTempRiseC: 3.5}).maxBatteryTempRiseC).toBe(3.5);
  expect(parseConfig(legacy)).not.toHaveProperty('orderSeed');
  expect(parseConfig({...bundled, orderSeed: 0}).orderSeed).toBe(0);
});

test('an expected model SHA-256 is optional and kept exactly', () => {
  const sha = '0123456789abcdef'.repeat(4);
  expect(parseConfig({...bundled, runs: [{...bundled.runs[0], modelSha256: sha}]}).runs[0].modelSha256).toBe(sha);
  const {modelSha256, ...unpinned} = bundled.runs[0];
  expect(modelSha256).toMatch(/^[0-9a-f]{64}$/);
  expect(parseConfig({...bundled, runs: [unpinned]}).runs[0]).not.toHaveProperty('modelSha256');
});

test('the experiment-only charging readiness flag is kept only when set', () => {
  expect(parseConfig(experiment)).toEqual(experiment);
  expect(parseConfig(bundled)).not.toHaveProperty('experimentChargingReadiness');
  expect(parseConfig({...bundled, experimentChargingReadiness: 'record-only'}).experimentChargingReadiness)
    .toBe('record-only');
});
