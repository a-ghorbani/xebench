import * as FS from '@dr.pogodin/react-native-fs';
import {initLlama, LlamaContext} from 'llama.rn';
import {runReference} from '../src/harness/reference';
import bundled from '../configs/cpu-reference-v2.json';
import v1 from '../configs/cpu-reference-v1.json';
import v3 from '../configs/cpu-reference-v3.json';
import {seededOrder} from '../src/harness/order';
import {NativeModules} from 'react-native';

const {createHash} = require('crypto');

jest.mock('@dr.pogodin/react-native-fs', () => ({
  ExternalDirectoryPath: '/fixture', exists: jest.fn(), stat: jest.fn(),
  readFile: jest.fn(), writeFile: jest.fn(), hash: jest.fn(), mkdir: jest.fn(),
}));
jest.mock('react-native', () => {
  const sample = {batteryPct: 80, batteryTempC: 30, charging: true, batteryStatus: 5, plugged: 1, currentNowUa: 0,
    powerSaveMode: false, thermalStatus: 0,
    thermalHeadroom: 0.3, screenOn: true, foreground: true, keyguardLocked: false, focusLossCount: 0,
    processUptimeMs: 1000, rssMb: 100, pssMb: 50, timestampMs: 1, elapsedMs: 1000};
  return {
    Platform: {OS: 'android', constants: {Model: 'Fixture', Release: '16'}, Version: 36},
    NativeModules: {BenchConditions: {
      sample: async () => sample, startMonitor: async () => ({status: 'started'}),
      stopMonitor: async () => ({status: 'stopped', intervalMs: 1000, startedElapsedMs: 1000,
        stoppedElapsedMs: 1200, ticks: 1, dropped: 0, expired: false,
        maxSampleCostMs: 1, totalSampleCostMs: 1, samples: [sample]}),
    }, BuildInfo: {identity: jest.fn(), nativeLibraries: jest.fn()}},
  };
});
jest.mock('llama.rn', () => ({initLlama: jest.fn(), BuildInfo: {number: '11385', commit: 'bf9a0cc'}}));

let files: Map<string, string>;
let output: jest.SpyInstance;
// The bundled configuration pins the reference GGUF; the mocked model has exactly those bytes.
const MODEL_SHA = bundled.runs[0].modelSha256;
const DEBUG_CERT = 'fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c';
const BUILD = {gitCommit: '0123456789abcdef0123456789abcdef01234567', gitDirty: false, flavor: 'reference',
  buildType: 'release', versionName: '1.0-reference', versionCode: 1, signingCertSha256: [DEBUG_CERT],
  bundledConfigSha256: 'b'.repeat(64)};
// Fields llama.rn (0.12.4 to 0.13.0-rc.8) sets on the LlamaContext returned by initLlama().
const CONTEXT = {gpu: false, reasonNoGPU: 'GPU backend is not available', devices: [],
  androidLib: 'rnllama_jni_v8_2_dotprod_i8mm',
  systemInfo: 'system_info: n_threads = 4 (n_threads_batch = 4) / 8 | CPU : NEON = 1 | ARM_FMA = 1 | DOTPROD = 1 | '};
// What BuildInfo.nativeLibraries() reports once llama.rn has loaded its library.
const LIBRARIES = [{name: 'librnllama_jni_v8_2_dotprod_i8mm.so', buildId: 'c'.repeat(40)},
  {name: 'librnllama_v8_2_dotprod_i8mm.so', buildId: 'd'.repeat(40)}];
const saved = () => jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));

beforeEach(() => {
  jest.clearAllMocks();
  files = new Map([['/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0})]]);
  jest.mocked(FS.exists).mockImplementation(async path => files.has(path));
  jest.mocked(FS.stat).mockResolvedValue({size: 100} as FS.StatResultT);
  jest.mocked(FS.readFile).mockImplementation(async path => files.get(path)!);
  jest.mocked(FS.writeFile).mockImplementation(async (path, text) => {files.set(path, text);});
  jest.mocked(FS.hash).mockImplementation(async path => path.endsWith('.gguf') ? MODEL_SHA :
    createHash('sha256').update(files.get(path)!).digest('hex'));
  jest.mocked(initLlama).mockResolvedValue({
    ...CONTEXT,
    completion: async (_options: unknown, token: () => void) => {
      token();
      return {timings: {prompt_n: 512, predicted_n: 128, prompt_ms: 10, predicted_ms: 20,
        prompt_per_second: 51200, predicted_per_second: 6400}};
    },
    release: jest.fn(async () => {}),
  } as unknown as LlamaContext);
  jest.mocked(NativeModules.BuildInfo.identity).mockResolvedValue(BUILD);
  jest.mocked(NativeModules.BuildInfo.nativeLibraries).mockResolvedValue(LIBRARIES);
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
  expect(record.artifact).toEqual({sha256: MODEL_SHA, sizeBytes: 100, expectedSha256: MODEL_SHA});
  expect(record.coldRuns[0]).toMatchObject({promptTokens: 512, decodeTokens: 128, status: 'complete'});
  expect(record.engineVersion).toContain('llama.rn');
  expect(record.configuration.nColdRuns).toBe(1);
});

test('invalid staged configuration fails before model hashing or native loading', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 0}));
  await expect(runReference(() => {})).rejects.toThrow();
  expect(jest.mocked(FS.hash).mock.calls.filter(([path]) => path.endsWith('.gguf'))).toEqual([]);
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
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0, maxDecodeTokens: 1}));
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
  expect(records.every(record => record.conditionVerdict.charging === 'satisfied')).toBe(true);
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
    return path.endsWith('.gguf') ? MODEL_SHA : createHash('sha256').update(files.get(path)!).digest('hex');
  });
  await runReference(() => {});
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  const missing = records.find(record => record.configurationRunId === 'missing');
  expect(missing).toMatchObject({status: 'failed', stopReason: 'setup-failed', artifact: null, coldRuns: []});
  expect(JSON.stringify(missing)).not.toContain('/private');
  expect(records.find(record => record.configurationRunId !== 'missing').status).toBe('complete');
  expect(initLlama).toHaveBeenCalledTimes(1);
});

test('every run, including the first, cools down after model hashing', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 1,
    runs: [bundled.runs[0], {...bundled.runs[0], id: 'second-model'}]}));
  await runReference(() => {});
  const records = jest.mocked(FS.writeFile).mock.calls.map(([, text]) => JSON.parse(text));
  expect(records.map(record => record.coldRuns[0].cooldownMs)).toEqual([1000, 1000]);
});

test('records the engine settings actually passed and what the loaded context reported', async () => {
  await runReference(() => {});
  const [record] = saved();
  const passed = jest.mocked(initLlama).mock.calls[0][0] as unknown as Record<string, unknown>;
  // Exactly the recorded init settings plus the (unrecorded, private) model path.
  expect(passed).toEqual({model: '/fixture/Llama-3.2-1B-Instruct-Q4_K_M.gguf', ...record.effectiveSettings.init});
  // cpu-reference-v2: PocketPal's Android load defaults (no mmap or mlock, repacking on, flash attention off).
  expect(record.effectiveSettings).toEqual({binding: 'llama.rn', unlisted: 'binding-defaults',
    init: {n_ctx: 2048, n_batch: 512, n_ubatch: 512, n_threads: 4, n_gpu_layers: 0, use_mmap: false, use_mlock: false,
      no_extra_bufts: false, flash_attn_type: 'off', cache_type_k: 'f16', cache_type_v: 'f16'},
    completion: {n_predict: 128, temperature: 0, seed: 42},
    // Vanilla npm llama.rn, exactly the pinned version, no patches.
    distribution: {source: 'npm', package: 'llama.rn', version: '0.13.0-rc.8', patches: []}});
  expect(record.engineVersion).toBe('llama.rn 0.13.0-rc.8');
  // llama.cpp's own build from llama.rn's exported BuildInfo; the other runtimes take no part.
  expect(record.runtimeVersions).toEqual({llamaCpp: {buildNumber: 11385, commit: 'bf9a0cc'},
    llamaRn: '0.13.0-rc.8', litertLm: null, litertNpuRuntimeQualcomm: null, qairt: null, genie: null,
    executorch: null, reactNativeExecutorch: null});
  expect(record.executionReports).toEqual([{runIndex: 0, ...CONTEXT, nativeLibraries: LIBRARIES}]);
  // Engine reports are evidence, not verified execution: the observed backend stays unknown.
  expect(record.observedBackend).toBeNull();
  expect(record.protocol.version).toBe('cpu-reference-v3');
});

test('a staged cpu-reference-v1 run keeps the harness defaults and its configuration verbatim', async () => {
  const staged = {...v1, nColdRuns: 1, cooldownSec: 0};
  const text = JSON.stringify(staged);
  files.set('/fixture/xebench-config.json', text);
  await runReference(() => {});
  const [record] = saved();
  expect(record.configuration).toEqual(staged);
  expect(record.configuration.runs[0]).not.toHaveProperty('useMmap');
  expect(record.configurationSource).toEqual({kind: 'staged', sha256: createHash('sha256').update(text).digest('hex')});
  expect(record.effectiveSettings.init).toEqual({n_ctx: 2048, n_batch: 512, n_ubatch: 512, n_threads: 4,
    n_gpu_layers: 0, use_mmap: true, use_mlock: false, no_extra_bufts: false, flash_attn_type: 'auto',
    cache_type_k: 'f16', cache_type_v: 'f16'});
  expect(initLlama).toHaveBeenCalledWith(expect.objectContaining({use_mmap: true, flash_attn_type: 'auto'}));
});

test('a staged cpu-reference-v3 file runs all three quantizations with its load settings', async () => {
  const staged = {...v3, nColdRuns: 1, cooldownSec: 0};
  files.set('/fixture/xebench-config.json', JSON.stringify(staged));
  const shas: Record<string, string> = Object.fromEntries(v3.runs.map(run => [run.modelFile, run.modelSha256]));
  jest.mocked(FS.hash).mockImplementation(async path => path.endsWith('.gguf') ? shas[path.split('/').pop()!] :
    createHash('sha256').update(files.get(path)!).digest('hex'));
  await runReference(() => {});
  const records = saved();
  expect(records.map(record => record.quant).sort()).toEqual(['Q4_0', 'Q4_K_M', 'Q8_0']);
  for (const record of records) {
    expect(record).toMatchObject({status: 'complete', configuration: staged});
    expect(record.artifact.expectedSha256).toBe(shas[record.modelFile]);
    expect(record.effectiveSettings.init).toMatchObject({use_mmap: false, use_mlock: false, no_extra_bufts: false,
      flash_attn_type: 'auto', n_gpu_layers: 0, n_threads: 4, n_ctx: 2048});
  }
  expect(jest.mocked(initLlama).mock.calls.map(([params]) => (params as {model: string}).model.split('/').pop()).sort())
    .toEqual(v3.runs.map(run => run.modelFile).sort());
});

test('each configured load setting reaches initLlama on its own', async () => {
  for (const [field, param, value] of [['useMmap', 'use_mmap', true], ['useMlock', 'use_mlock', true],
    ['noExtraBufts', 'no_extra_bufts', true], ['flashAttnType', 'flash_attn_type', 'on']] as const) {
    jest.mocked(initLlama).mockClear();
    jest.mocked(FS.writeFile).mockClear();
    files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0,
      runs: [{...bundled.runs[0], [field]: value}]}));
    await runReference(() => {});
    expect(initLlama).toHaveBeenCalledWith(expect.objectContaining({[param]: value}));
    expect(saved()[0].effectiveSettings.init[param]).toBe(value);
  }
});

test('unreported or malformed context fields stay unknown', async () => {
  const context = await jest.mocked(initLlama)({} as never);
  jest.mocked(initLlama).mockResolvedValue({...context, gpu: 'no', reasonNoGPU: 'x'.repeat(300),
    devices: ['CPU', '\u0000'], androidLib: '../librnllama.so', systemInfo: undefined} as unknown as LlamaContext);
  await runReference(() => {});
  expect(saved()[0].executionReports).toEqual([
    {runIndex: 0, gpu: null, reasonNoGPU: null, devices: null, androidLib: null, systemInfo: null,
      nativeLibraries: LIBRARIES}]);
});

// The bundled configuration has a 30 s cooldown; an unreadable model records without waiting.
const missingModel = () => jest.mocked(FS.hash).mockImplementation(async path => {
  if (path.endsWith('.gguf')) throw new Error('missing');
  return createHash('sha256').update(files.get(path)!).digest('hex');
});

test('records build identity and the bundled configuration hash from the build', async () => {
  files.delete('/fixture/xebench-config.json');
  missingModel();
  await runReference(() => {});
  const record = saved()[0];
  const {bundledConfigSha256, ...identity} = BUILD;
  expect(record.build).toEqual({status: 'reported', ...identity});
  expect(record.configurationSource).toEqual({kind: 'bundled', sha256: bundledConfigSha256});
  expect(record).toMatchObject({configuration: bundled, stopReason: 'setup-failed', artifact: null});
});

test('a staged configuration is identified by the SHA-256 of the bytes read', async () => {
  await runReference(() => {});
  const text = files.get('/fixture/xebench-config.json')!;
  expect(saved()[0].configurationSource).toEqual({kind: 'staged',
    sha256: createHash('sha256').update(text).digest('hex')});
});

test('a configuration that changes while it is read is refused before any run', async () => {
  let reads = 0;
  jest.mocked(FS.hash).mockImplementation(async () => (reads++ ? 'e' : 'f').repeat(64));
  await expect(runReference(() => {})).rejects.toThrow('changed');
  expect(initLlama).not.toHaveBeenCalled();
  expect(FS.writeFile).not.toHaveBeenCalled();
});

test('without the BuildInfo module the build is recorded as unavailable, never guessed', async () => {
  files.delete('/fixture/xebench-config.json');
  jest.mocked(NativeModules.BuildInfo.identity).mockRejectedValue(new Error('no module'));
  missingModel();
  await runReference(() => {});
  const record = saved()[0];
  expect(record.build).toEqual({status: 'unavailable', gitCommit: null, gitDirty: null, flavor: null,
    buildType: null, versionName: null, versionCode: null, signingCertSha256: null});
  expect(record.configurationSource).toEqual({kind: 'bundled', sha256: null});
});

test('malformed build values are dropped individually', async () => {
  jest.mocked(NativeModules.BuildInfo.identity).mockResolvedValue({...BUILD, gitCommit: 'HEAD', gitDirty: 'yes',
    flavor: 'custom', versionName: '/sdcard/x', versionCode: -1, signingCertSha256: ['AB']});
  await runReference(() => {});
  expect(saved()[0].build).toEqual({status: 'reported', gitCommit: null, gitDirty: null, flavor: null,
    buildType: 'release', versionName: null, versionCode: null, signingCertSha256: null});
});

test('without a configured model hash the expectation is recorded as null', async () => {
  const {modelSha256, ...run} = bundled.runs[0];
  expect(modelSha256).toMatch(/^[0-9a-f]{64}$/);
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0, runs: [run]}));
  await runReference(() => {});
  expect(saved()[0]).toMatchObject({status: 'complete',
    artifact: {sha256: MODEL_SHA, sizeBytes: 100, expectedSha256: null}});
});

test('a model hash mismatch is a failed record and aborts the configuration', async () => {
  const runs = [{...bundled.runs[0], modelSha256: 'c'.repeat(64)}, {...bundled.runs[0], id: 'second-model'}];
  // A seed that executes the mismatching run first.
  const orderSeed = [1, 2, 3, 4, 5].find(seed => seededOrder(runs.map(run => run.id), seed)[0] === runs[0].id);
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0, orderSeed, runs}));
  await expect(runReference(() => {})).rejects.toThrow('does not match');
  expect(initLlama).not.toHaveBeenCalled();
  const written = saved();
  expect(written.map(record => [record.configurationRunId, record.status, record.stopReason])).toEqual([
    [runs[0].id, 'failed', 'model-hash-mismatch'], ['second-model', 'failed', 'configuration-aborted']]);
  expect(written[1].artifact).toBeNull();
  const mismatch = written[0];
  expect(mismatch).toMatchObject({status: 'failed', coldRuns: [],
    artifact: {sha256: MODEL_SHA, sizeBytes: 100, expectedSha256: 'c'.repeat(64)}});
  expect(mismatch.guardNotes).toContain('Not run: the model artifact does not match the configured SHA-256');
  expect(output.mock.calls.some(([line]) => line === 'XEBENCH_ERROR llama32-q4km-cpu: model-hash-mismatch')).toBe(true);
});

test('a debug build loads JS from Metro, so its bundled configuration hash is unknown', async () => {
  files.delete('/fixture/xebench-config.json');
  missingModel();
  jest.mocked(NativeModules.BuildInfo.identity).mockResolvedValue({...BUILD, buildType: 'debug'});
  await runReference(() => {});
  expect(saved()[0].configurationSource).toEqual({kind: 'bundled', sha256: null});
  expect(saved()[0].build.buildType).toBe('debug');
});
