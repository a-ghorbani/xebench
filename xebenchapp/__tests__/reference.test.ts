import * as FS from '@dr.pogodin/react-native-fs';
import {initLlama, LlamaContext} from 'llama.rn';
import {runReference} from '../src/harness/reference';
import bundled from '../configs/cpu-reference-v1.json';
import {seededOrder} from '../src/harness/order';

const {createHash} = require('crypto');

jest.mock('@dr.pogodin/react-native-fs', () => ({
  ExternalDirectoryPath: '/fixture', exists: jest.fn(), stat: jest.fn(),
  readFile: jest.fn(), writeFile: jest.fn(), hash: jest.fn(), mkdir: jest.fn(),
}));
jest.mock('react-native', () => {
  const sample = {batteryPct: 80, batteryTempC: 30, charging: true, powerSaveMode: false, thermalStatus: 0,
    thermalHeadroom: null, screenOn: true, foreground: true, keyguardLocked: false, focusLossCount: 0,
    processUptimeMs: 1000, rssMb: 100, pssMb: 50, timestampMs: 1};
  return {
    Platform: {OS: 'android', constants: {Model: 'Fixture', Release: '16'}, Version: 36},
    NativeModules: {BenchConditions: {
      sample: async () => sample, startMonitor: async () => ({status: 'started'}),
      stopMonitor: async () => ({status: 'stopped', intervalMs: 1000, ticks: 1, dropped: 0, expired: false,
        maxSampleCostMs: 1, totalSampleCostMs: 1, samples: [sample]}),
    }},
  };
});
jest.mock('llama.rn', () => ({initLlama: jest.fn()}));

let files: Map<string, string>;
let output: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  files = new Map([['/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0})]]);
  jest.mocked(FS.exists).mockImplementation(async path => files.has(path));
  jest.mocked(FS.stat).mockResolvedValue({size: 100} as FS.StatResultT);
  jest.mocked(FS.readFile).mockImplementation(async path => files.get(path)!);
  jest.mocked(FS.writeFile).mockImplementation(async (path, text) => {files.set(path, text);});
  jest.mocked(FS.hash).mockImplementation(async path => path.endsWith('.gguf') ? 'a'.repeat(64) :
    createHash('sha256').update(files.get(path)!).digest('hex'));
  jest.mocked(initLlama).mockResolvedValue({
    completion: async (_options: unknown, token: () => void) => {
      token();
      return {timings: {prompt_n: 512, predicted_n: 128, prompt_ms: 10, predicted_ms: 20,
        prompt_per_second: 51200, predicted_per_second: 6400}};
    },
    release: jest.fn(async () => {}),
  } as unknown as LlamaContext);
  output = jest.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {output.mockRestore();});

test('configuration reaches CPU engine and checksummed evidence file', async () => {
  await runReference(() => {});
  expect(initLlama).toHaveBeenCalledWith(expect.objectContaining({n_threads: 4, n_ctx: 2048, n_gpu_layers: 0}));
  const marker = output.mock.calls.find(([line]) => line.startsWith('XEBENCH_RESULT_FILE '))![0];
  expect(marker.length).toBeLessThan(256);
  const reference = JSON.parse(marker.slice('XEBENCH_RESULT_FILE '.length));
  const data = files.get(`/fixture/xebench-results/${reference.name}`)!;
  expect(reference.sha256).toBe(createHash('sha256').update(data).digest('hex'));
  const record = JSON.parse(data);
  expect(record.status).toBe('complete');
  expect(record.artifact).toEqual({sha256: 'a'.repeat(64), sizeBytes: 100});
  expect(record.coldRuns[0]).toMatchObject({promptTokens: 512, decodeTokens: 128, status: 'complete'});
  expect(record.engineVersion).toContain('llama.rn');
  expect(record.configuration.nColdRuns).toBe(1);
});

test('invalid staged configuration fails before hashing or native loading', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 0}));
  await expect(runReference(() => {})).rejects.toThrow();
  expect(FS.hash).not.toHaveBeenCalled();
  expect(initLlama).not.toHaveBeenCalled();
});

test('repeated invocations write different evidence filenames', async () => {
  await runReference(() => {});
  await runReference(() => {});
  const paths = jest.mocked(FS.writeFile).mock.calls.map(([path]) => path);
  expect(new Set(paths).size).toBe(2);
});

test('file write failure releases the engine and emits no dangling reference', async () => {
  jest.mocked(FS.writeFile).mockRejectedValueOnce(new Error('fixture disk full'));
  await expect(runReference(() => {})).rejects.toThrow('fixture disk full');
  const context = await jest.mocked(initLlama).mock.results[0].value;
  expect(context.release).toHaveBeenCalled();
  expect(output).not.toHaveBeenCalled();
});

test('records Android release rather than SDK level', async () => {
  await runReference(() => {});
  const data = jest.mocked(FS.writeFile).mock.calls[0][1];
  expect(JSON.parse(data).deviceInfo.androidRelease).toBe('16');
});

test.each([false, true])('cleanup failure aborts later models after saving evidence (inference failure: %s)', async inferenceFails => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0,
    runs: [bundled.runs[0], {...bundled.runs[0], id: 'second-model'}]}));
  const context = await jest.mocked(initLlama)({} as never);
  jest.mocked(initLlama).mockClear();
  jest.mocked(context.release).mockRejectedValueOnce(new Error('fixture cleanup failure'));
  if (inferenceFails) {
    context.completion = jest.fn().mockRejectedValueOnce(new Error('fixture inference failure'));
  }

  await expect(runReference(() => {})).rejects.toThrow('cleanup');
  expect(initLlama).toHaveBeenCalledTimes(1);
  expect(context.release).toHaveBeenCalledTimes(1);
  expect(FS.writeFile).toHaveBeenCalledTimes(2);
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  // The later model is recorded as explicitly not run rather than silently dropped.
  expect(records[1]).toMatchObject({status: 'failed', stopReason: 'configuration-aborted', coldRuns: [],
    artifact: null, configurationRunId: records[0].runOrder.order[1]});
  const record = records[0];
  expect(record.status).toBe('failed');
  expect(record.coldRuns[0]).toMatchObject({
    error: {phase: inferenceFails ? 'inference' : 'release'},
    cleanupError: {phase: 'release', message: 'fixture cleanup failure'},
  });
  expect(record.summary.decodeTps).toBeNull();
  expect(output.mock.calls.some(([line]) => line.startsWith('XEBENCH_RESULT_FILE '))).toBe(true);
});

test('one-token native result without timed decode is saved as failed, not zero throughput', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, maxDecodeTokens: 1}));
  const release = jest.fn(async () => {});
  jest.mocked(initLlama).mockResolvedValue({
    completion: async (_options: unknown, token: () => void) => {
      token();
      // Pinned llama.rn clamps the timing count to 1 even with no decode step.
      return {timings: {prompt_n: 472, predicted_n: 1, prompt_ms: 10, predicted_ms: 0,
        prompt_per_second: 47200, predicted_per_second: 0}};
    }, release,
  } as unknown as LlamaContext);
  await runReference(() => {});
  const record = JSON.parse(jest.mocked(FS.writeFile).mock.calls[0][1]);
  expect(record.status).toBe('failed');
  expect(record.coldRuns[0]).toMatchObject({status: 'failed', error: {phase: 'inference'}});
  expect(record.summary.decodeTps).toBeNull();
  expect(release).toHaveBeenCalledTimes(1);
  expect(output.mock.calls.some(([line]) => line.startsWith('XEBENCH_ERROR '))).toBe(true);
});

test('run order is shuffled by a recorded seed and reproducible from a configured seed', async () => {
  const runs = ['a', 'b', 'c', 'd'].map(id => ({...bundled.runs[0], id}));
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0, runs, orderSeed: 12345}));
  await runReference(() => {});
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  const order = seededOrder(runs.map(run => run.id), 12345);
  expect(order).not.toEqual(['a', 'b', 'c', 'd']);
  expect(records.map(record => record.configurationRunId)).toEqual(order);
  expect(records.map(record => record.runOrder.position)).toEqual([0, 1, 2, 3]);
  expect(records[0].runOrder).toMatchObject({method: 'mulberry32-fisher-yates-v1', seed: 12345,
    seedSource: 'configured', order});
  expect(records.every(record => record.conditionVerdict.charging === 'violated')).toBe(true);
});

test('a generated order seed is recorded', async () => {
  await runReference(() => {});
  const record = JSON.parse(jest.mocked(FS.writeFile).mock.calls[0][1]);
  expect(record.runOrder).toMatchObject({seedSource: 'generated', seed: expect.any(Number), position: 0});
});

test('an unreadable model is recorded as a failed run and later runs still execute', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0, orderSeed: 1,
    runs: [bundled.runs[0], {...bundled.runs[0], id: 'missing', modelFile: 'missing.gguf'}]}));
  jest.mocked(FS.hash).mockImplementation(async path => {
    if (path.endsWith('missing.gguf')) throw new Error('/private/path/missing.gguf not found');
    return path.endsWith('.gguf') ? 'a'.repeat(64) : createHash('sha256').update(files.get(path)!).digest('hex');
  });
  await runReference(() => {});
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  const missing = records.find(record => record.configurationRunId === 'missing');
  expect(missing).toMatchObject({status: 'failed', stopReason: 'setup-failed', artifact: null, coldRuns: []});
  expect(JSON.stringify(missing)).not.toContain('/private');
  expect(records.find(record => record.configurationRunId !== 'missing').status).toBe('complete');
  expect(initLlama).toHaveBeenCalledTimes(1);
});

test('a later run in the configuration gets the cooldown before its first repetition', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 1,
    runs: [bundled.runs[0], {...bundled.runs[0], id: 'second-model'}]}));
  await runReference(() => {});
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  expect(records.map(record => record.coldRuns[0].cooldownMs)).toEqual([0, 1000]);
});
