import {parseConfig} from '../src/harness/config';
import bundled from '../configs/cpu-reference-v1.json';
import experiment from '../configs/experiment-charging-ab-v1.json';

test('bundled CPU reference configuration is valid', () => {
  expect(parseConfig(bundled)).toEqual(bundled);
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

test('the experiment-only charging readiness flag is kept only when set', () => {
  expect(parseConfig(experiment)).toEqual(experiment);
  expect(parseConfig(bundled)).not.toHaveProperty('experimentChargingReadiness');
  expect(parseConfig({...bundled, experimentChargingReadiness: 'record-only'}).experimentChargingReadiness)
    .toBe('record-only');
});
