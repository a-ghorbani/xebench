import {GenieXAdapter, GenieXProfile, genieXEngineVersion, genieXMetrics, genieXRuntimeVersions}
  from '../src/harness/adapters/GenieXAdapter';
import {bundleArtifact, bundleListing, BundleFs} from '../src/harness/bundle';
import {parseAcceleratorConfig} from '../src/harness/config';
import {acceleratorAdapter} from '../src/harness/reference';
import {STANDARD_PROMPT} from '../src/harness/prompts';

const mockGenerate = jest.fn();
const mockLoad = jest.fn();
const mockRelease = jest.fn();
jest.mock('react-native', () => ({Platform: {OS: 'android'}, NativeModules: {GenieXBench: {
  generate: (...args: unknown[]) => mockGenerate(...args), load: (...args: unknown[]) => mockLoad(...args),
  release: (...args: unknown[]) => mockRelease(...args),
  getConstants: () => ({geniexVersion: '0.8.0', qairtVersion: '2.50.0.260828221209'}),
}}}));
jest.mock('@dr.pogodin/react-native-fs', () => ({}));
jest.mock('llama.rn', () => ({}));

const BUNDLE = 'qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy';
const npu = () => new GenieXAdapter({modelPath: `/files/${BUNDLE}`, backend: 'npu', quantLabel: 'W4A16',
  modelLabel: 'Qwen3 1.7B', nThreads: null, nCtx: null});
const cpu = () => new GenieXAdapter({modelPath: '/files/Qwen3-1.7B-Q4_0.gguf', backend: 'cpu', quantLabel: 'Q4_0',
  modelLabel: 'Qwen3 1.7B', nThreads: 4, nCtx: 4096});

/** ProfilingData as GenieXBenchModule returns it, with the SDK's own speeds (counts over times). */
function profile(overrides: Partial<GenieXProfile> = {}): GenieXProfile {
  const base = {ttftMs: 140, promptTimeMs: 140, decodeTimeMs: 3200, promptTokens: 532, generatedTokens: 128,
    stopReason: 'length', ...overrides};
  return {prefillSpeed: base.promptTokens / (base.promptTimeMs / 1000),
    decodingSpeed: base.generatedTokens / (base.decodeTimeMs / 1000), ...base, ...overrides};
}

beforeEach(() => {
  mockGenerate.mockReset();
  mockLoad.mockReset();
  mockRelease.mockReset();
  mockLoad.mockResolvedValue({loadMs: 2100, librariesBefore: ['libgeniex.so'],
    librariesAfter: ['libQnnHtp.so', 'libQnnHtpV81Stub.so', 'libgeniex.so']});
});

describe('genieXMetrics', () => {
  test('NPU: prefill is prompt tokens over the QAIRT TTFT; decode excludes the end-of-turn token', () => {
    const metrics = genieXMetrics('npu', 151.5, profile({generatedTokens: 97, stopReason: 'eos'}), 128);
    expect(metrics).toMatchObject({ttftMs: 151.5, promptTokens: 532, prefillMethod: 'ttft-derived',
      decodeTokens: 96, decodeMethod: 'engine-timings'});
    expect(metrics.prefillTps).toBeCloseTo(532 / 0.14, 6);
    expect(metrics.decodeTps).toBeCloseTo(96 / 3.2, 6);
    expect(metrics.engineInternal).toEqual({promptMs: 140, promptPerSecond: metrics.prefillTps, predictedMs: 3200,
      predictedPerSecond: metrics.decodeTps});
  });

  test('a decode that reaches the limit counts every token', () => {
    const metrics = genieXMetrics('npu', 150, profile({generatedTokens: 128, stopReason: 'length'}), 128);
    expect(metrics.decodeTokens).toBe(128);
  });

  test('CPU (llama_cpp plugin): prefill batches are engine timings and the count has no end-of-turn token', () => {
    const metrics = genieXMetrics('cpu', 900, profile({ttftMs: 880, promptTimeMs: 850, generatedTokens: 96,
      stopReason: 'eos'}), 128);
    expect(metrics).toMatchObject({prefillMethod: 'engine-timings', decodeTokens: 96});
    expect(metrics.prefillTps).toBeCloseTo(532 / 0.85, 6);
  });

  test.each([
    ['no profile', 'npu', 150, null, 'no profiling data'],
    ['no first token', 'npu', null, profile(), 'non-finite'],
    ['a user stop', 'npu', 150, profile({stopReason: 'user'}), 'stopped for user'],
    ['a stop word', 'npu', 150, profile({stopReason: 'stop_sequence'}), 'stopped for stop_sequence'],
    ['a prompt time that is not the TTFT', 'npu', 150, profile({promptTimeMs: 120}), 'not its TTFT'],
    ['no decoded token', 'npu', 150, profile({generatedTokens: 1, stopReason: 'eos'}), 'no decoded token'],
    ['more than the limit', 'npu', 150, profile({generatedTokens: 129, stopReason: 'length'}), 'more than the limit'],
    ['speeds that are not counts over times', 'npu', 150, profile({decodingSpeed: 41}), 'no longer hold'],
  ])('%s is refused', (_name, backend, ttft, data, message) => {
    expect(() => genieXMetrics(backend as 'npu', ttft, data as GenieXProfile | null, 128)).toThrow(message as string);
  });
});

test('versions: geniex-android always, QAIRT on the NPU only; the label mirrors raw_session.py', () => {
  const constants = {geniexVersion: '0.8.0', qairtVersion: '2.50.0.260828221209'};
  expect(genieXRuntimeVersions(constants, 'npu')).toMatchObject({genie: '0.8.0', qairt: '2.50.0.260828221209',
    litertLm: null, llamaRn: null});
  expect(genieXRuntimeVersions(constants, 'cpu')).toMatchObject({genie: '0.8.0', qairt: null});
  expect(genieXRuntimeVersions({}, 'npu')).toMatchObject({genie: null, qairt: null});
  expect(npu().engineVersion()).toBe('GENIE-X geniex-android 0.8.0 + QAIRT 2.50.0.260828221209 (npu)');
  expect(cpu().engineVersion()).toBe('GENIE-X geniex-android 0.8.0 (cpu)');
  expect(genieXEngineVersion('npu', genieXRuntimeVersions({}, 'npu'))).toBe(
    'GENIE-X geniex-android unknown + QAIRT unknown (npu)');
});

test('load creates from genie_config.json on the NPU and the GGUF on the CPU; evidence is the newly mapped runtime', async () => {
  const adapter = npu();
  await expect(adapter.load()).resolves.toBe(2100);
  expect(mockLoad).toHaveBeenLastCalledWith(`/files/${BUNDLE}/genie_config.json`, 'npu', 0, 0);
  expect(adapter.backendEvidence()).toMatchObject({status: 'consistent', source: 'process-libraries'});
  await cpu().load();
  expect(mockLoad).toHaveBeenLastCalledWith('/files/Qwen3-1.7B-Q4_0.gguf', 'cpu', 4, 4096);
  mockLoad.mockResolvedValue({loadMs: 2000, librariesBefore: ['libQnnHtp.so'], librariesAfter: ['libQnnHtp.so']});
  const later = npu();
  await later.load();
  expect(later.backendEvidence().status).toBe('unknown');
  mockLoad.mockResolvedValue({});
  await expect(npu().load()).rejects.toThrow('no duration');
  expect(() => new GenieXAdapter({modelPath: '/files/x.gguf', backend: 'cpu', quantLabel: 'Q4_0',
    modelLabel: 'Qwen3 1.7B', nThreads: null, nCtx: null})).toThrow('need nThreads and nCtx');
});

test('requests send the pre-templated prompt verbatim, for the gate and the measurement', async () => {
  const adapter = npu();
  mockGenerate.mockResolvedValue({text: 'Paris', ttftMs: 150, profile: profile()});
  await expect(adapter.generate({prompt: '<|im_start|>user\nCapital?', text: 'Capital?', maxDecodeTokens: 32}))
    .resolves.toBe('Paris');
  expect(mockGenerate).toHaveBeenLastCalledWith('<|im_start|>user\nCapital?', 32);
  const metrics = await adapter.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128});
  expect(mockGenerate).toHaveBeenLastCalledWith(STANDARD_PROMPT, 128);
  expect(metrics.decodeTokens).toBe(128);
  mockGenerate.mockResolvedValue({ttftMs: 150, profile: profile()});
  await expect(adapter.generate({prompt: 'x', maxDecodeTokens: 4})).rejects.toThrow('no text');
  await adapter.release();
  expect(mockRelease).toHaveBeenCalled();
});

test('accelerator runs build GENIE-X adapters with their backend and CPU settings', () => {
  const config = parseAcceleratorConfig({schemaVersion: 2, id: 'geniex', nColdRuns: 3, cooldownSec: 120,
    readinessTimeoutSec: 900, maxBatteryTempRiseC: 2, maxDecodeTokens: 128, chatTemplate: 'qwen25', runs: [
      {id: 'cpu', engine: 'genie-x', backend: 'cpu', purpose: 'measurement', model: 'Qwen3 1.7B',
        modelFile: 'Qwen3-1.7B-Q4_0.gguf', quant: 'Q4_0', nThreads: 4, nCtx: 4096, referenceRunId: null},
      {id: 'npu', engine: 'genie-x', backend: 'npu', purpose: 'measurement', model: 'Qwen3 1.7B', modelFile: BUNDLE,
        quant: 'W4A16', nThreads: null, nCtx: null, referenceRunId: 'cpu'}]});
  const [reference, candidate] = config.runs.map(run => acceleratorAdapter(run, `/files/${run.modelFile}`));
  expect([reference.engine, reference.backend, reference.settings()]).toEqual(['genie-x', 'cpu',
    {nThreads: 4, nCtx: 4096}]);
  expect([candidate.engine, candidate.backend, candidate.settings()]).toEqual(['genie-x', 'npu',
    {nThreads: null, nCtx: null}]);
});

describe('GENIE-X configuration rules', () => {
  const base = {schemaVersion: 2, id: 'geniex', nColdRuns: 3, cooldownSec: 120, readinessTimeoutSec: 900,
    maxBatteryTempRiseC: 2, maxDecodeTokens: 128, chatTemplate: 'qwen25'};
  const cpuRun = {id: 'cpu', engine: 'genie-x', backend: 'cpu', purpose: 'measurement', model: 'Qwen3 1.7B',
    modelFile: 'Qwen3-1.7B-Q4_0.gguf', quant: 'Q4_0', nThreads: 4, nCtx: 4096, referenceRunId: null};
  const npuRun = {id: 'npu', engine: 'genie-x', backend: 'npu', purpose: 'measurement', model: 'Qwen3 1.7B',
    modelFile: BUNDLE, quant: 'W4A16', nThreads: null, nCtx: null, referenceRunId: 'cpu'};
  test.each([
    ['a GPU run', [cpuRun, {...npuRun, backend: 'gpu'}], 'backend cpu'],
    ['a bundle on the CPU', [{...cpuRun, modelFile: BUNDLE}], '.gguf basename on CPU'],
    ['a GGUF on the NPU', [cpuRun, {...npuRun, modelFile: 'Qwen3-1.7B-Q4_0.gguf'}], 'bundle directory'],
    ['a path as the bundle', [cpuRun, {...npuRun, modelFile: '../bundle'}], 'bundle directory'],
    ['NPU threads', [cpuRun, {...npuRun, nThreads: 4}], 'NPU runs take null'],
    ['no CPU context size', [{...cpuRun, nCtx: null}], 'CPU runs need nThreads and nCtx'],
    ['llama.cpp offload settings', [{...cpuRun, nGpuLayers: 0}], 'llama.cpp only'],
    ['a reference of another engine', [{...cpuRun, engine: 'llama.cpp', nGpuLayers: 0}, npuRun], 'same engine'],
    ['fixed decoding', [cpuRun, npuRun], 'LiteRT-LM runs only'],
  ])('%s is refused', (name, runs, message) => {
    const input = {...base, runs, ...(name === 'fixed decoding' ? {decodeMode: 'fixed'} : {})};
    expect(() => parseAcceleratorConfig(input)).toThrow(message);
  });

  test('a CPU reference and an NPU bundle parse as given', () => {
    const input = {...base, measuredPrompt: 'std-pp512-v1', runs: [cpuRun, npuRun]};
    expect(parseAcceleratorConfig(input)).toEqual(input);
  });
});

describe('bundle digest', () => {
  const files: Record<string, string> = {'tokenizer_config.json': 'b'.repeat(64), 'tokenizer.json': 'a'.repeat(64),
    'part1_of_4.bin': 'c'.repeat(64)};
  const fs = (entries: {name: string; size: number; file?: boolean}[]): BundleFs & {written: string[]} => {
    const written: string[] = [];
    return {
      written,
      readDir: async directory => entries.map(entry => ({name: entry.name, path: `${directory}/${entry.name}`,
        size: entry.size, isFile: () => entry.file !== false})),
      hash: async path => path === '/cache/listing' ? 'f'.repeat(64) : files[path.split('/').pop()!],
      writeFile: async (_path, contents) => {
        written.push(contents);
      },
      unlink: async () => {},
    };
  };

  test('the listing is sha256sum output sorted by byte order, and the artifact is its hash', async () => {
    const io = fs([{name: 'tokenizer_config.json', size: 5}, {name: 'tokenizer.json', size: 11},
      {name: 'part1_of_4.bin', size: 600}]);
    await expect(bundleArtifact(io, '/files/bundle', '/cache/listing')).resolves.toEqual({sha256: 'f'.repeat(64),
      sizeBytes: 616});
    expect(io.written).toEqual([`${'c'.repeat(64)}  part1_of_4.bin\n${'a'.repeat(64)}  tokenizer.json\n` +
      `${'b'.repeat(64)}  tokenizer_config.json\n`]);
    expect(bundleListing([{name: 'b', sha256: '2'}, {name: 'a', sha256: '1'}])).toBe('1  a\n2  b\n');
  });

  test('an empty bundle, a subdirectory or an odd name is refused', async () => {
    await expect(bundleArtifact(fs([]), '/files/bundle', '/cache/listing')).rejects.toThrow('empty');
    await expect(bundleArtifact(fs([{name: 'sub', size: 0, file: false}]), '/files/bundle', '/cache/listing'))
      .rejects.toThrow('regular files');
    await expect(bundleArtifact(fs([{name: 'a b.bin', size: 1}]), '/files/bundle', '/cache/listing'))
      .rejects.toThrow('plain names');
  });
});
