import {parseConfig} from '../src/harness/config';
import bundled from '../configs/cpu-reference-v1.json';

test('bundled CPU reference configuration is valid', () => {
  expect(parseConfig(bundled)).toEqual(bundled);
});

test.each([
  {schemaVersion: 2}, {nColdRuns: 0}, {nColdRuns: 2.5}, {cooldownSec: -1},
  {maxDecodeTokens: 0}, {runs: []}, {nGpuLayers: 99},
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
