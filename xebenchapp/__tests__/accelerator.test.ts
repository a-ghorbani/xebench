/**
 * accelerator-v1: configuration parsing, the output-correctness gate, observed-backend
 * evidence and whole-configuration runs. Records produced here are compared with the
 * committed fixture tests/fixtures/raw-session/app-emitted-accelerator.json, which
 * tests/test_raw_session.py validates with the host validator and
 * tests/test_export_evidence.py exports and verifies. After an intended record change,
 * regenerate with XEBENCH_UPDATE_FIXTURES=1 and update the schema.
 */
import {AcceleratorConfig, AcceleratorRun, parseAcceleratorConfig} from '../src/harness/config';
import {
  AcceleratorIO, AcceleratorSession, runAcceleratorConfiguration, unmeasuredAcceleratorSession, gateNotRun,
} from '../src/harness/accelerator';
import {
  answerPassed, commonPrefix, comparisonStatus, correctnessStatus, PROBES, probeResult, referenceCorrectness,
} from '../src/harness/gate';
import {assessBackend, summarizeBackend} from '../src/harness/backend';
import {liteRtEngineVersion, liteRtRuntimeVersions} from '../src/harness/adapters/LiteRtAdapter';
import {genieXEngineVersion, genieXMetrics, genieXRuntimeVersions} from '../src/harness/adapters/GenieXAdapter';
import {NO_RUNTIME_VERSIONS} from '../src/harness/runtime';
import {
  executorchEffectiveSettings, executorchEngineVersion, executorchMetrics, executorchPromptPrefix, executorchRuntimeVersions,
} from '../src/harness/executorch';
import type {EngineAdapter, LlamaRnEffectiveSettings} from '../src/harness/adapters/EngineAdapter';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';
import {ORDER_METHOD, seededOrder} from '../src/harness/order';
import f8Config from '../configs/f8-gemma3-accelerators-v1.json';
import s23Config from '../configs/s23-gemma3-accelerators-v1.json';
import bugReproConfig from '../configs/s23-litert-npu-bugrepro-v1.json';
import llamaHexagonConfig from '../configs/f8-llama32-hexagon-check-v1.json';
import qwenOpenclConfig from '../configs/llamacpp-qwen25-opencl-v1.json';
import {STANDARD_PROMPT, STANDARD_PROMPT_LABEL} from '../src/harness/prompts';
import q4kmHexagonConfig from '../configs/llamacpp-llama32-q4km-hexagon-v1.json';
import litertQwenCpuConfig from '../configs/litertlm-qwen25-q8-cpu-v1.json';
import litertGemmaNpuConfig from '../configs/litertlm-gemma3-sm8850-npu-v1.json';
import litertGemmaNpuFixedConfig from '../configs/litertlm-gemma3-sm8850-npu-v2.json';

declare const __dirname: string;
declare const process: {env: Record<string, string | undefined>};
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.join(ROOT, 'tests/fixtures/raw-session/app-emitted-accelerator.json');
const SCHEMA = JSON.parse(fs.readFileSync(path.join(ROOT, 'schemas/raw-session-v1.json'), 'utf8'));
const SESSION = SCHEMA.$defs.sessionAcceleratorV2;

const healthy = {timestampMs: 1767225600000, elapsedMs: 5000, batteryPct: 80, batteryTempC: 30.5, charging: true,
  batteryStatus: 4, plugged: 2, currentNowUa: -1500,
  powerSaveMode: false, thermalStatus: 0, thermalHeadroom: 0.4, screenOn: true, foreground: true,
  keyguardLocked: false, focusLossCount: 0, processUptimeMs: 60000, rssMb: 1800.5, pssMb: 412.25};
const monitor = {status: 'stopped', intervalMs: 1000, startedElapsedMs: 5000, stoppedElapsedMs: 6500, ticks: 2,
  dropped: 0, expired: false, maxSampleCostMs: 2.5, totalSampleCostMs: 4,
  samples: [healthy, {...healthy, elapsedMs: 6000}]};

beforeEach(() => {
  jest.spyOn(conditions, 'readConditions').mockImplementation(async () => conditions.normalizeConditions(healthy));
  jest.spyOn(conditions, 'startMonitor').mockResolvedValue('started');
  jest.spyOn(conditions, 'stopMonitor').mockImplementation(async () => conditions.normalizeMonitor(monitor));
});
afterEach(() => jest.restoreAllMocks());

// The experimental flavor's identity, as BuildInfo reports it (cpu-reference-v3 fields).
const BUILD = {status: 'reported' as const, gitCommit: '0123456789abcdef0123456789abcdef01234567', gitDirty: false,
  flavor: 'experimental' as const, buildType: 'release' as const, versionName: '1.0-experimental', versionCode: 1,
  signingCertSha256: ['fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c']};
const SOURCE = {kind: 'staged' as const, sha256: 'e'.repeat(64)};

const CORRECT: Record<string, string> = {
  'short-capital': 'Paris', 'long-fact-last': 'PELICAN', 'long-fact-first': 'PELICAN',
  'long-fact-middle': 'Room 417.', 'measured-prompt': 'Measurement moved from anatomy to physical constants.',
};

interface FakeOptions {
  answers?: Record<string, string>;
  gpu?: boolean | null;
  devices?: string[] | null;
  libraries?: {before: string[] | null; after: string[] | null};
  failLoadAt?: number[];
  failGate?: boolean;
  failRelease?: boolean;
  /** ExecuTorch: the program's delegates for forward (default XnnpackBackend). */
  delegates?: string[];
}

/** An engine adapter that answers gate probes from a table and reports fixed timings. */
function fake(run: AcceleratorRun, options: FakeOptions = {}): EngineAdapter & {loads: number} {
  const answers = {...CORRECT, ...options.answers};
  let measured = 0;
  // decodeMode fixed (LiteRtAdapter.prepareMeasurement): repetitions decode exactly this many tokens.
  let fixedDecodeTokens = 0;
  // What the engines report from the APK: llama.rn's BuildInfo, LiteRtBenchModule's constants.
  const versions = () => run.engine === 'litert-lm' ? liteRtRuntimeVersions({litertLmVersion: '0.18.0',
    litertNpuRuntimeQualcommVersion: '2.3.0', qairtVersion: '2.50.0.260828221209'}, run.backend) :
    run.engine === 'executorch' ? executorchRuntimeVersions({executorchVersion: '1.4.1'}, '0.10.7') :
    run.engine === 'genie-x' ? genieXRuntimeVersions({geniexVersion: '0.8.0', qairtVersion: '2.50.0.260828221209'},
      run.backend) :
      {...NO_RUNTIME_VERSIONS, llamaCpp: {buildNumber: 11385, commit: 'bf9a0cc'}, llamaRn: '0.0.0-fixture'};
  const adapter = {
    engine: run.engine, backend: run.backend, modelLabel: run.model, quantLabel: run.quant, modelFile: run.modelFile,
    loads: 0,
    engineVersion: () => run.engine === 'litert-lm' ? liteRtEngineVersion(run.backend, versions()) :
      run.engine === 'executorch' ? executorchEngineVersion(versions()) :
      run.engine === 'genie-x' ? genieXEngineVersion(run.backend, versions()) :
        `llama.rn 0.0.0-fixture${run.backend === 'cpu' ? '' : ` (${run.backend})`}`,
    runtimeVersions: () => versions(),
    settings: () => ({nThreads: run.nThreads, nCtx: run.nCtx}),
    load: async () => {
      adapter.loads++;
      if (options.failLoadAt?.includes(adapter.loads)) throw new Error('fixture load failure');
      if (options.failGate && adapter.loads === 1) throw new Error('fixture gate load failure');
      return 1500 + adapter.loads;
    },
    release: async () => {
      if (options.failRelease) throw new Error('fixture release failure');
    },
    generate: async (cfg: {text?: string}) => {
      const probe = PROBES.find(item => item.prompt === cfg.text)!;
      return answers[probe.id];
    },
    benchOnce: async (cfg: {maxDecodeTokens: number}): Promise<SingleRunMetrics> => {
      measured++;
      if (run.engine === 'executorch') {
        // What TextLLMRunner reports for a 523-token prompt (two 512-token prefill forwards).
        const generated = (fixedDecodeTokens || 97) - 1;
        const start = 1767225600000 + measured * 100000;
        return executorchMetrics({stats: {numPromptTokens: 523, numGeneratedTokens: generated,
          inferenceStartMs: start, firstTokenMs: start + 1040 + measured, inferenceEndMs: start + 1040 + measured +
            generated * 52}, start: 5000, first: 6062.5 + measured, end: 12700, emitted: generated + 1}, 512,
        fixedDecodeTokens);
      }
      if (run.engine === 'genie-x') {
        // GENIE-X's ProfilingData as GenieXBenchModule returns it: the QAIRT plugin reports its TTFT as
        // the prompt time and counts the end-of-turn token; the llama_cpp plugin times the prefill alone.
        const npu = run.backend === 'npu';
        const ttft = npu ? 140 + measured : 900 + measured;
        const promptTimeMs = npu ? ttft : ttft - 30;
        const decodeTimeMs = 2900 + measured * 10;
        const generatedTokens = npu ? 97 : 96;
        return genieXMetrics(run.backend, ttft + 2.5, {ttftMs: ttft, promptTimeMs, decodeTimeMs, promptTokens: 532,
          generatedTokens, prefillSpeed: 532 / (promptTimeMs / 1000),
          decodingSpeed: generatedTokens / (decodeTimeMs / 1000), stopReason: 'eos'}, cfg.maxDecodeTokens);
      }
      return {loadMs: 0, ttftMs: 120 + measured, promptTokens: 503, decodeTokens: fixedDecodeTokens || 96,
        prefillTps: 900 + measured * 3.5, decodeTps: 40 + measured * 0.25,
        prefillMethod: 'engine-timings', decodeMethod: 'engine-timings'};
    },
    backendEvidence: () => {
      if (run.engine === 'executorch') {
        const libraries = {before: ['libexecutorch.so', 'libRnExecutorch.so',
          'libxnnpack_executorch_backend.so'], after: ['libexecutorch.so', 'libRnExecutorch.so',
          'libxnnpack_executorch_backend.so']};
        const program = {methods: [{name: 'forward', delegates: options.delegates ?? ['XnnpackBackend']},
          {name: 'get_max_seq_len', delegates: []}], registeredBackends: ['XnnpackBackend']};
        return {status: assessBackend(run.engine, run.backend, {report: null, libraries, program}),
          source: 'program-delegates' as const, report: null, libraries, program};
      }
      const report = run.engine === 'llama.cpp' ? llamaReport() : null;
      // The first load maps the accelerator runtime; it stays mapped for later loads.
      const markers = run.engine === 'genie-x' ? (run.backend === 'npu' ? ['libQnnHtp.so', 'libQnnHtpV81Stub.so',
        'libcdsprpc.so', 'libgeniex_plugin_qairt.so'] : ['libgeniex_plugin_llama_cpp.so', 'libggml-cpu.so']) :
        run.backend === 'npu' ? ['libLiteRtDispatch_Qualcomm.so', 'libQnnHtp.so'] :
        run.backend === 'gpu' ? ['libOpenCL.so'] : [];
      const libraries = run.engine !== 'llama.cpp' ? options.libraries ??
        {before: adapter.loads > 1 ? markers : [], after: markers} : null;
      return {status: assessBackend(run.engine, run.backend, {report, libraries}),
        source: report ? 'engine-report' as const : 'process-libraries' as const, report, libraries};
    },
  };
  // What llama.rn reports after a load, as LlamaRnAdapter.executionReport() returns it.
  const llamaReport = () => {
    const gpu = options.gpu ?? run.backend !== 'cpu';
    return {gpu, devices: options.devices === undefined ? (run.devices ?? (run.backend === 'gpu' ? ['GPUOpenCL'] : [])) :
      options.devices, androidLib: 'rnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl',
    reasonNoGpu: gpu ? null : 'GPU backend is available but was not selected'};
  };
  if (run.engine === 'executorch') {
    Object.assign(adapter, {
      prepareMeasurement: (measurement: {decodeMode: 'fixed'; decodeTokens: number}) => {
        fixedDecodeTokens = measurement.decodeTokens;
      },
      effectiveSettings: (request: {maxDecodeTokens: number; decodeMode?: 'fixed'}) =>
        // EXECUTORCH_CONFIG uses the gemma4 template: Gemma prompts carry a literal <bos>.
        executorchEffectiveSettings(request, {version: '0.10.7', nativeLibsVersion: '0.10.6'}, executorchPromptPrefix('gemma4')),
    });
  }
  if (run.engine === 'litert-lm') {
    Object.assign(adapter, {
      prepareMeasurement: (measurement: {decodeMode: 'fixed'; decodeTokens: number}) => {
        fixedDecodeTokens = measurement.decodeTokens;
      },
    });
  }
  if (run.engine === 'llama.cpp') {
    Object.assign(adapter, {
      effectiveSettings: ({maxDecodeTokens}: {maxDecodeTokens: number}) => ({binding: 'llama.rn' as const,
        init: {n_ctx: run.nCtx!, n_batch: 512, n_ubatch: 512, n_threads: run.nThreads, n_gpu_layers: run.nGpuLayers ?? 0,
          // The run's load settings, else LlamaRnAdapter's defaults.
          use_mmap: run.useMmap ?? true, use_mlock: run.useMlock ?? false, no_extra_bufts: run.noExtraBufts ?? false,
          flash_attn_type: run.flashAttnType ?? 'auto' as const, cache_type_k: 'f16' as const,
          cache_type_v: 'f16' as const, ...(run.devices ? {devices: [...run.devices]} : {})},
        completion: {n_predict: maxDecodeTokens, temperature: 0, seed: 42}, unlisted: 'binding-defaults' as const,
        distribution: {source: 'npm' as const, package: 'llama.rn' as const, version: '0.0.0-fixture',
          patches: [] as []}}),
      executionReport: () => {
        const report = llamaReport();
        return {gpu: report.gpu, reasonNoGPU: report.reasonNoGpu,
          devices: report.devices, androidLib: report.androidLib,
          systemInfo: 'system_info: n_threads = 4 / 8 | CPU : NEON = 1 | ',
          nativeLibraries: [{name: 'librnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl.so', buildId: 'c'.repeat(40)},
            {name: 'librnllama_v8_2_dotprod_i8mm_hexagon_opencl.so', buildId: 'd'.repeat(40)}]};
      },
    });
  }
  return adapter;
}

/** The staged bytes match the configured pin unless a test says otherwise. */
const SHA = (run: AcceleratorRun) => run.modelSha256 ?? (run.modelFile.startsWith('Gemma3') ? 'b'.repeat(64) :
  run.modelFile.endsWith('.gguf') ? 'c'.repeat(64) : 'd'.repeat(64));
const TOKENIZER_SHA = (run: AcceleratorRun) => run.tokenizerSha256 ?? 'a'.repeat(64);

/** An ExecuTorch CPU configuration as the board would stage it: std-pp512-v1 and a fixed 128-token decode. */
const EXECUTORCH_CONFIG = {schemaVersion: 2, id: 'executorch-gemma4-cpu-fixture', nColdRuns: 2, cooldownSec: 120,
  readinessTimeoutSec: 900, maxBatteryTempRiseC: 2, maxDecodeTokens: 128, chatTemplate: 'gemma4',
  measuredPrompt: 'std-pp512-v1', decodeMode: 'fixed', runs: [{id: 'gemma4-e2b-8da4w-pte-cpu', engine: 'executorch',
    backend: 'cpu', purpose: 'measurement', model: 'Gemma 4 E2B', modelFile: 'gemma_4_e2b_xnnpack_8da4w.pte',
    quant: '8da4w', nThreads: null, nCtx: null, referenceRunId: null,
    tokenizerFile: 'gemma_4_e2b_tokenizer.json',
    modelSha256: '3'.repeat(64), tokenizerSha256: '4'.repeat(64)}]};

async function runConfiguration(config: AcceleratorConfig, options: Record<string, FakeOptions> = {},
  extra: Partial<AcceleratorIO> = {}) {
  const records: Record<string, AcceleratorSession> = {};
  // A configuration without orderSeed gets a generated seed, as the app does.
  const seed = config.orderSeed ?? 7;
  const runs = seededOrder(config.runs, seed);
  const order = runs.map(run => run.id);
  let sequence = 0;
  const outcome = await runAcceleratorConfiguration(config, runs, (_run, position) => ({method: ORDER_METHOD,
    seed, seedSource: config.orderSeed === undefined ? 'generated' : 'configured', order, position}), {
    artifact: async run => ({sha256: SHA(run), sizeBytes: 600000000}),
    tokenizer: async run => ({sha256: TOKENIZER_SHA(run), sizeBytes: 32000000}),
    adapter: run => fake(run, options[run.id]),
    identity: () => ({runId: `1767225600000-${sequence++}-accel`, timestampIso: '2026-01-01T00:00:00.000Z',
      deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: null, build: BUILD,
      configurationSource: SOURCE}),
    persister: run => async record => {records[run.id] = JSON.parse(JSON.stringify(record));},
    log: () => {},
    sleep: async () => {},
    now: () => 0,
    ...extra,
  }).then(() => null, (error: Error) => error);
  for (const record of Object.values(records)) {
    for (const attempt of record.coldRuns) {
      if (attempt.instance) attempt.instance.processInstanceId = 'fixture-process';
    }
  }
  return {records, error: outcome};
}

const f8 = parseAcceleratorConfig(f8Config);
const small = (config: AcceleratorConfig, nColdRuns = 2) => ({...config, nColdRuns});

/**
 * GENIE-X in-app NPU on the QAIRT bundle, gated against GENIE-X's llama_cpp plugin on the Q4_0 GGUF
 * (sameArtifact false). The ChatML template stands in for Qwen3's in this fixture.
 */
const GENIEX_CONFIG = {schemaVersion: 2, id: 'fixture-geniex-qwen3', nColdRuns: 3, cooldownSec: 120,
  readinessTimeoutSec: 900, maxBatteryTempRiseC: 2, maxDecodeTokens: 128, chatTemplate: 'qwen25',
  measuredPrompt: 'std-pp512-v1', orderSeed: 11, runs: [
    {id: 'qwen3-1.7b-q40-cpu', engine: 'genie-x', backend: 'cpu', purpose: 'measurement', model: 'Qwen3 1.7B',
      modelFile: 'Qwen3-1.7B-Q4_0.gguf', quant: 'Q4_0', nThreads: 4, nCtx: 4096, referenceRunId: null,
      modelSha256: 'a'.repeat(64)},
    {id: 'qwen3-1.7b-w4a16-npu', engine: 'genie-x', backend: 'npu', purpose: 'measurement', model: 'Qwen3 1.7B',
      modelFile: 'qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy', quant: 'W4A16',
      nThreads: null, nCtx: null, referenceRunId: 'qwen3-1.7b-q40-cpu',
      modelSha256: 'c41b7071b62e791b924254f1e8ed3245ee74825013c1723e3a62e8877b2ab5c2'},
  ]};

test('versioned matrix configurations parse and pair every accelerator with a CPU reference', () => {
  const s23 = parseAcceleratorConfig(s23Config);
  const bug = parseAcceleratorConfig(bugReproConfig);
  for (const config of [f8, s23, bug]) {
    expect(config.nColdRuns).toBeGreaterThanOrEqual(config === bug ? 5 : 10);
    expect(config.orderSeed).toEqual(expect.any(Number));
    for (const run of config.runs.filter(item => item.backend !== 'cpu')) {
      const reference = config.runs.find(item => item.id === run.referenceRunId)!;
      expect([reference.backend, reference.engine]).toEqual(['cpu', run.engine]);
    }
  }
  expect(f8.runs.map(run => `${run.engine}/${run.backend}`)).toEqual(['litert-lm/cpu', 'litert-lm/gpu',
    'litert-lm/npu', 'llama.cpp/cpu', 'llama.cpp/gpu', 'llama.cpp/npu']);
  expect(s23.runs.map(run => `${run.engine}/${run.backend}`)).toEqual(['litert-lm/cpu', 'litert-lm/gpu',
    'llama.cpp/cpu', 'llama.cpp/gpu']);
  expect(bug.runs.find(run => run.backend === 'npu')!.purpose).toBe('bug-repro');
  // Legacy-row check: llama.cpp Hexagon on Llama 3.2 1B, gated against llama.cpp CPU.
  const legacy = parseAcceleratorConfig(llamaHexagonConfig);
  expect(legacy.chatTemplate).toBe('llama32');
  expect(legacy.runs.map(run => `${run.backend}<-${run.referenceRunId}`)).toEqual(['cpu<-null',
    'npu<-llama32-q4km-cpu', 'cpu<-null', 'npu<-llama32-q40-cpu']);
  // The parsed configuration is exactly what is echoed into records.
  expect(f8).toEqual(f8Config);
  // Every committed accelerator run pins its model bytes.
  for (const config of [f8, s23, bug, parseAcceleratorConfig(llamaHexagonConfig)]) {
    for (const run of config.runs) expect(run.modelSha256).toMatch(/^[0-9a-f]{64}$/);
  }
});

test.each([
  [{schemaVersion: 1}],
  [{chatTemplate: 'llama3'}],
  [{maxBatteryTempRiseC: undefined}],
  [{runs: [{...f8Config.runs[1]}]}],
  [{runs: [f8Config.runs[0], {...f8Config.runs[1], referenceRunId: 'llama-cpu'}, f8Config.runs[3]]}],
  [{runs: [{...f8Config.runs[0], referenceRunId: 'litert-cpu'}]}],
  [{runs: [{...f8Config.runs[0], modelFile: 'gemma.gguf'}]}],
  [{runs: [{...f8Config.runs[0], nCtx: 2048}]}],
  [{runs: [{...f8Config.runs[3], nGpuLayers: 99}]}],
  [{runs: [f8Config.runs[3], {...f8Config.runs[5], devices: ['GPUOpenCL']}]}],
  [{runs: [f8Config.runs[3], {...f8Config.runs[4], devices: ['HTP0']}]}],
  [{runs: [{...f8Config.runs[3], nGpuLayers: undefined}]}],
  [{runs: [{...f8Config.runs[0], purpose: 'demo'}]}],
  [{runs: [{...f8Config.runs[0], modelFile: '../escape.litertlm'}]}],
  [{runs: [{...f8Config.runs[0], extra: 1}]}],
  [{runs: [{...f8Config.runs[0], modelSha256: 'ABC'}]}],
])('invalid accelerator configuration %j is rejected', change => {
  const input = JSON.parse(JSON.stringify({...f8Config, ...change}));
  expect(() => parseAcceleratorConfig(input)).toThrow();
});

test('gate verdicts: word-boundary answers, reference agreement and comparison validity', () => {
  expect(answerPassed('The code word is **PELICAN**.', 'pelican')).toBe(true);
  expect(answerPassed('pelicans', 'pelican')).toBe(false);
  expect(answerPassed('Room 4170', '417')).toBe(false);
  expect(commonPrefix('  abc😀d ', 'abc😀e')).toBe(4);
  const probe = PROBES[1];
  expect(probeResult(probe, ' PELICAN', {output: 'PELICAN', passed: true})).toMatchObject(
    {passed: true, exactMatch: true, commonPrefixChars: 7});
  const ok = [probeResult(probe, 'PELICAN', {output: 'PELICAN', passed: true})];
  const bad = [probeResult(probe, 'the the the', {output: 'PELICAN', passed: true})];
  expect(correctnessStatus('candidate', null, ok, 'pass')).toEqual({status: 'pass', reasons: []});
  expect(correctnessStatus('candidate', null, bad, 'pass')).toEqual({status: 'fail', reasons: ['answer-mismatch']});
  expect(correctnessStatus('candidate', null, ok, null).reasons).toEqual(['reference-unavailable']);
  expect(correctnessStatus('candidate', null, ok, 'fail').reasons).toEqual(['reference-failed']);
  expect(correctnessStatus('candidate', {phase: 'load', message: 'x'}, ok, 'pass').reasons).toEqual(['gate-error']);
  // A wrong answer stays decisive when a later probe errors.
  expect(correctnessStatus('candidate', {phase: 'inference', message: 'x'}, bad, 'pass').status).toBe('fail');
  expect(comparisonStatus('measurement', {status: 'pass'}, true)).toEqual(
    {status: 'invalid', reasons: ['backend-mismatch']});
  expect(correctnessStatus('reference', null, [], null).reasons).toEqual(['gate-not-run']);
  const reference = referenceCorrectness(128, {outputs: PROBES.map(item => CORRECT[item.id]), error: null});
  expect(reference.status).toBe('pass');
  expect(comparisonStatus('measurement', reference)).toEqual({status: 'valid', reasons: []});
  expect(comparisonStatus('bug-repro', reference)).toEqual({status: 'invalid', reasons: ['bug-repro']});
  expect(comparisonStatus('measurement', {...reference, status: 'fail'}).status).toBe('invalid');
  expect(comparisonStatus('measurement', {...reference, status: 'unverified'}).status).toBe('unverified');
  // The gate covers more than one 128-token prefill chunk (LiteRT-LM#3508): ~4 chars/token.
  expect(PROBES.filter(item => item.kind === 'answer' && item.prompt.length > 4 * 128).length).toBeGreaterThanOrEqual(3);
});

test('backend evidence: engine reports confirm, loaded runtimes are only consistent, unknown stays unknown', () => {
  const report = (gpu: boolean | null, devices: string[] | null) =>
    ({report: {gpu, devices, androidLib: null, reasonNoGpu: null}, libraries: null});
  expect(assessBackend('llama.cpp', 'cpu', report(false, []))).toBe('confirmed');
  // Attached GPU devices at nGpuLayers 0 (seen on the F8 Ultra) are not a mismatch.
  expect(assessBackend('llama.cpp', 'cpu', report(true, ['GPUOpenCL', 'HTP0']))).toBe('consistent');
  expect(assessBackend('llama.cpp', 'gpu', report(true, ['GPUOpenCL']))).toBe('confirmed');
  expect(assessBackend('llama.cpp', 'gpu', report(false, []))).toBe('mismatch');
  expect(assessBackend('llama.cpp', 'npu', report(true, ['GPUOpenCL']))).toBe('mismatch');
  expect(assessBackend('llama.cpp', 'npu', report(true, ['HTP0']))).toBe('confirmed');
  expect(assessBackend('llama.cpp', 'npu', report(true, null))).toBe('consistent');
  expect(assessBackend('llama.cpp', 'gpu', report(null, null))).toBe('unknown');
  const libraries = (before: string[] | null, after: string[] | null) => ({report: null, libraries: {before, after}});
  expect(assessBackend('litert-lm', 'npu', libraries([], ['libLiteRtDispatch_Qualcomm.so']))).toBe('consistent');
  expect(assessBackend('litert-lm', 'npu', libraries([], []))).toBe('unknown');
  expect(assessBackend('litert-lm', 'gpu', libraries(null, ['libOpenCL.so']))).toBe('unknown');
  expect(assessBackend('litert-lm', 'gpu', libraries([], ['libOpenCL.so']))).toBe('consistent');
  // Already mapped before this engine was created (gate, earlier run, llama.rn OpenCL): no evidence.
  expect(assessBackend('litert-lm', 'gpu', libraries(['libOpenCL.so'], ['libOpenCL.so']))).toBe('unknown');
  expect(assessBackend('litert-lm', 'npu',
    libraries(['libLiteRtDispatch_Qualcomm.so'], ['libLiteRtDispatch_Qualcomm.so']))).toBe('unknown');
  expect(assessBackend('litert-lm', 'cpu', libraries([], ['libOpenCL.so']))).toBe('unknown');
  expect(assessBackend('litert-lm', 'cpu', libraries(['libOpenCL.so'], ['libOpenCL.so']))).toBe('consistent');
  expect(assessBackend('litert-lm', 'cpu', libraries(null, []))).toBe('unknown');
  expect(assessBackend('litert-lm', 'gpu', {report: null, libraries: null})).toBe('unknown');
  const item = (status: 'confirmed' | 'consistent' | 'mismatch' | 'unknown') =>
    ({status, source: 'engine-report' as const, report: null, libraries: null});
  expect(summarizeBackend([item('confirmed'), item('consistent')])!.status).toBe('consistent');
  expect(summarizeBackend([item('confirmed'), item('mismatch'), item('unknown')])!.status).toBe('mismatch');
  expect(summarizeBackend([])).toBeNull();
});

test('references are gated first; candidates are compared, measured even when they fail, and marked', async () => {
  const order: string[] = [];
  const {records, error} = await runConfiguration(small(f8, 3), {
    'litert-npu': {answers: {'long-fact-last': 'the the the the', 'measured-prompt': 'the the the'}},
  }, {
    adapter: run => {
      const adapter = fake(run, run.id === 'litert-npu' ? {answers: {'long-fact-last': 'the the the'}} : {});
      const load = adapter.load;
      adapter.load = async () => {order.push(run.id); return load();};
      return adapter;
    },
  });
  expect(error).toBeNull();
  // Both CPU references load first (their gates), in configuration order.
  expect(order.slice(0, 2)).toEqual(['litert-cpu', 'llama-cpu']);
  expect(Object.values(records).every(record => record.status === 'complete')).toBe(true);
  const npu = records['litert-npu'];
  expect(npu.correctness).toMatchObject({role: 'candidate', status: 'fail', reasons: ['answer-mismatch'],
    reference: {configurationRunId: 'litert-cpu', status: 'pass', sameArtifact: false}});
  expect(npu.comparison).toEqual({status: 'invalid', reasons: ['correctness-fail']});
  expect(npu.summary.decodeTps!.n).toBe(3);
  expect(records['litert-gpu'].correctness.reference!.sameArtifact).toBe(true);
  expect(records['litert-gpu'].comparison.status).toBe('valid');
  expect(records['litert-cpu'].correctness.role).toBe('reference');
  expect(records['llama-hexagon'].observedBackend).toEqual({status: 'confirmed', source: 'engine-report',
    repetitions: 3});
  expect(records['llama-hexagon'].guardNotes).not.toContain('Actual backend execution has not been verified');
  // LiteRT-LM evidence: only the gate (first load) sees the NPU runtime being loaded.
  expect(records['litert-npu'].gateBackend!.status).toBe('consistent');
  expect(records['litert-npu'].observedBackend!.status).toBe('unknown');
  expect(records['llama-hexagon'].gateBackend!.status).toBe('confirmed');
  expect(records['litert-npu'].guardNotes).toContain('Actual backend execution has not been verified');
});

async function emitted(): Promise<Record<string, AcceleratorSession>> {
  const out: Record<string, AcceleratorSession> = {};
  const base = await runConfiguration(small(f8), {
    'litert-npu': {answers: {'long-fact-last': 'the the the the', 'measured-prompt': 'the the the'}},
    'llama-opencl': {gpu: false, devices: []},
  });
  for (const [id, record] of Object.entries(base.records)) out[`f8-${id}`] = record;

  const bug = parseAcceleratorConfig(bugReproConfig);
  const repro = await runConfiguration(small(bug), {'litert-npu-sm8550': {libraries: {before: null, after: null}}});
  out['bugRepro-npu'] = repro.records['litert-npu-sm8550'];

  // The reference engine is missing: candidates are unverified; one repetition fails at load.
  const missing = await runConfiguration(small(bug, 3), {'litert-npu-sm8550': {failLoadAt: [3]}}, {
    adapter: run => {
      if (run.backend === 'cpu') throw new Error('not linked');
      return fake(run, {failLoadAt: [3]});
    },
  });
  out['referenceUnavailable-npu'] = missing.records['litert-npu-sm8550'];
  out['engineUnavailable-cpu'] = missing.records['litert-cpu'];

  // A gate that cannot load the engine still measures, unverified.
  const gateError = await runConfiguration(small(bug), {'litert-npu-sm8550': {failGate: true}});
  out['gateError-npu'] = gateError.records['litert-npu-sm8550'];

  // A gate cleanup failure stops the configuration; later runs are recorded as not run.
  const leaking = await runConfiguration(small(bug), {'litert-cpu': {failRelease: true}});
  expect(leaking.error).not.toBeNull();
  out['gateCleanupFailed-cpu'] = leaking.records['litert-cpu'];
  out['configurationAborted-npu'] = leaking.records['litert-npu-sm8550'];
  // A candidate whose own gate cannot release: recorded unmeasured, without sameArtifact.
  const leakingCandidate = await runConfiguration(small(bug), {'litert-npu-sm8550': {failRelease: true}});
  expect(leakingCandidate.error).not.toBeNull();
  out['gateCleanupFailedCandidate-npu'] = leakingCandidate.records['litert-npu-sm8550'];

  // The staged NPU bundle is not the pinned file: recorded unmeasured with the bytes it found.
  const wrong = await runConfiguration(small(f8, 1), {}, {
    artifact: async run => ({sha256: run.id === 'litert-npu' ? 'e'.repeat(64) : SHA(run), sizeBytes: 600000000}),
  });
  out['hashMismatch-npu'] = wrong.records['litert-npu'];

  // The committed llama.cpp board configurations, exactly as staged (v3 load settings).
  for (const [prefix, config] of [['qwen25', qwenOpenclConfig], ['board-hexagon', q4kmHexagonConfig],
    ['board-litert-qwen25', litertQwenCpuConfig], ['board-litert-npu', litertGemmaNpuConfig],
    ['board-litert-fixed', litertGemmaNpuFixedConfig]] as const) {
    const board = await runConfiguration(parseAcceleratorConfig(config));
    for (const [id, record] of Object.entries(board.records)) out[`${prefix}-${id}`] = record;
  }
  const geniex = await runConfiguration(parseAcceleratorConfig(GENIEX_CONFIG));
  for (const [id, record] of Object.entries(geniex.records)) out[`geniex-${id}`] = record;

  // ExecuTorch CPU (XNNPACK): measured, a tokenizer that is not the pinned file, an unreadable
  // tokenizer, and a program that delegates to another backend.
  const executorch = parseAcceleratorConfig(EXECUTORCH_CONFIG);
  out['executorch-cpu'] = (await runConfiguration(executorch)).records['gemma4-e2b-8da4w-pte-cpu'];
  out['executorch-tokenizerMismatch'] = (await runConfiguration(executorch, {}, {
    tokenizer: async () => ({sha256: 'f'.repeat(64), sizeBytes: 32000000}),
  })).records['gemma4-e2b-8da4w-pte-cpu'];
  out['executorch-tokenizerUnreadable'] = (await runConfiguration(executorch, {}, {
    tokenizer: async () => {
      throw new Error('missing');
    },
  })).records['gemma4-e2b-8da4w-pte-cpu'];
  out['executorch-vulkanProgram'] = (await runConfiguration(executorch,
    {'gemma4-e2b-8da4w-pte-cpu': {delegates: ['VulkanBackend']}})).records['gemma4-e2b-8da4w-pte-cpu'];

  const run = bug.runs[1];
  out['setupFailed-npu'] = unmeasuredAcceleratorSession(fake(run), small(bug), run,
    {runId: '1767225600000-99-accel', timestampIso: '2026-01-01T00:00:00.000Z',
      deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: null, build: BUILD,
      configurationSource: SOURCE}, null, 'setup-failed',
    {correctness: gateNotRun(run, small(bug)), gateBackend: null});
  return JSON.parse(JSON.stringify(out));
}

test('accelerator records match the committed fixture validated by the host schema', async () => {
  const records = await emitted();
  expect(Object.fromEntries(Object.entries(records).map(([key, value]) => [key,
    `${value.status}/${value.stopReason}/${value.correctness.status}/${value.comparison.status}/` +
    `${value.observedBackend?.status ?? null}/${value.gateBackend?.status ?? null}`]))).toEqual({
    'f8-litert-cpu': 'complete/null/pass/valid/consistent/consistent',
    'f8-litert-gpu': 'complete/null/pass/valid/unknown/consistent',
    'f8-litert-npu': 'complete/null/fail/invalid/unknown/consistent',
    'f8-llama-cpu': 'complete/null/pass/valid/confirmed/confirmed',
    'f8-llama-opencl': 'complete/null/pass/invalid/mismatch/mismatch',
    'f8-llama-hexagon': 'complete/null/pass/valid/confirmed/confirmed',
    'bugRepro-npu': 'complete/null/pass/invalid/unknown/unknown',
    'referenceUnavailable-npu': 'failed/repetition-failed/unverified/invalid/unknown/consistent',
    'engineUnavailable-cpu': 'failed/engine-unavailable/unverified/unverified/null/null',
    'gateError-npu': 'complete/null/unverified/invalid/unknown/null',
    'gateCleanupFailed-cpu': 'failed/gate-cleanup-failed/unverified/unverified/null/consistent',
    'gateCleanupFailedCandidate-npu': 'failed/gate-cleanup-failed/unverified/invalid/null/consistent',
    'configurationAborted-npu': 'failed/configuration-aborted/unverified/invalid/null/null',
    'setupFailed-npu': 'failed/setup-failed/unverified/invalid/null/null',
    'hashMismatch-npu': 'failed/model-hash-mismatch/unverified/unverified/null/null',
    'qwen25-qwen25-1.5b-q80-cpu': 'complete/null/pass/valid/confirmed/confirmed',
    'qwen25-qwen25-1.5b-q80-opencl': 'complete/null/pass/valid/confirmed/confirmed',
    'board-hexagon-llama32-q4km-cpu': 'complete/null/pass/valid/confirmed/confirmed',
    'board-hexagon-llama32-q4km-hexagon': 'complete/null/pass/valid/confirmed/confirmed',
    'board-litert-qwen25-qwen25-1.5b-q8-litertlm-cpu': 'complete/null/pass/valid/consistent/consistent',
    'board-litert-npu-gemma3-1b-int4-litertlm-cpu': 'complete/null/pass/valid/consistent/consistent',
    'board-litert-npu-gemma3-1b-sm8850-litertlm-npu': 'complete/null/pass/valid/unknown/consistent',
    'board-litert-fixed-gemma3-1b-int4-litertlm-cpu': 'complete/null/pass/valid/consistent/consistent',
    'board-litert-fixed-gemma3-1b-sm8850-litertlm-npu': 'complete/null/pass/valid/unknown/consistent',
    'executorch-cpu': 'complete/null/pass/valid/confirmed/confirmed',
    'executorch-tokenizerMismatch': 'failed/tokenizer-hash-mismatch/unverified/unverified/null/null',
    'executorch-tokenizerUnreadable': 'failed/setup-failed/unverified/unverified/null/null',
    'executorch-vulkanProgram': 'complete/null/pass/invalid/mismatch/mismatch',
    'geniex-qwen3-1.7b-q40-cpu': 'complete/null/pass/valid/consistent/consistent',
    'geniex-qwen3-1.7b-w4a16-npu': 'complete/null/pass/valid/unknown/consistent',
  });
  // ExecuTorch: the CPU reference prompt verbatim, a fixed 128-token decode, the tokenizer hashed.
  const executorchRecord = records['executorch-cpu'];
  expect(executorchRecord.protocol).toMatchObject({promptLabel: STANDARD_PROMPT_LABEL, prompt: STANDARD_PROMPT,
    promptFormat: 'raw-pretemplated', decodeMode: 'fixed'});
  expect(executorchRecord.coldRuns.map(attempt => attempt.status === 'complete' &&
    [attempt.decodeTokens, attempt.executorchStats!.numGeneratedTokens, attempt.executorchStats!.prefillChunks]))
    .toEqual([[128, 127, 2], [128, 127, 2]]);
  expect(executorchRecord.tokenizerArtifact).toEqual({file: 'gemma_4_e2b_tokenizer.json', sha256: '4'.repeat(64),
    sizeBytes: 32000000, expectedSha256: '4'.repeat(64)});
  expect(executorchRecord.effectiveSettings).toMatchObject({binding: 'react-native-executorch',
    generation: {maxNewTokens: 128, ignoreEos: true, temperature: 0, promptPrefix: '<bos>'}});
  expect(executorchRecord.runtimeVersions).toMatchObject({executorch: '1.4.1', reactNativeExecutorch: '0.10.7'});
  expect(records['executorch-tokenizerMismatch'].tokenizerArtifact!.sha256).toBe('f'.repeat(64));
  expect(records['executorch-tokenizerUnreadable'].tokenizerArtifact).toBeNull();
  expect(records['f8-llama-cpu']).not.toHaveProperty('tokenizerArtifact');
  // GENIE-X: the bundle is compared with a different reference file; versions and metrics as reported.
  const geniexNpu = records['geniex-qwen3-1.7b-w4a16-npu'];
  expect(geniexNpu.correctness.reference).toMatchObject({sameArtifact: false, modelFile: 'Qwen3-1.7B-Q4_0.gguf'});
  expect(geniexNpu.engineVersion).toBe('GENIE-X geniex-android 0.8.0 + QAIRT 2.50.0.260828221209 (npu)');
  expect(geniexNpu.runtimeVersions).toMatchObject({genie: '0.8.0', qairt: '2.50.0.260828221209'});
  expect(records['geniex-qwen3-1.7b-q40-cpu'].runtimeVersions).toMatchObject({genie: '0.8.0', qairt: null});
  expect(geniexNpu.effectiveSettings).toBeNull();
  expect(geniexNpu.protocol).not.toHaveProperty('decodeMode');
  const attempt = geniexNpu.coldRuns.find(item => item.status === 'complete')! as SingleRunMetrics;
  expect([attempt.decodeTokens, attempt.prefillMethod, attempt.decodeMethod]).toEqual([96, 'ttft-derived',
    'engine-timings']);
  // decodeMode fixed: the protocol says so and every repetition decoded exactly maxDecodeTokens.
  for (const key of ['board-litert-fixed-gemma3-1b-int4-litertlm-cpu', 'board-litert-fixed-gemma3-1b-sm8850-litertlm-npu']) {
    expect(records[key].protocol.decodeMode).toBe('fixed');
    expect(records[key].coldRuns.map(attempt => attempt.status === 'complete' ? attempt.decodeTokens : null))
      .toEqual(records[key].coldRuns.map(() => 128));
  }
  expect(records['board-litert-npu-gemma3-1b-sm8850-litertlm-npu'].protocol).not.toHaveProperty('decodeMode');
  // The board configurations' records carry exactly the committed files and their load settings.
  expect(records['qwen25-qwen25-1.5b-q80-opencl'].configuration).toEqual(qwenOpenclConfig);
  expect(records['board-hexagon-llama32-q4km-hexagon'].configuration).toEqual(q4kmHexagonConfig);
  expect((records['qwen25-qwen25-1.5b-q80-opencl'].effectiveSettings as LlamaRnEffectiveSettings).init).toMatchObject({use_mmap: false,
    use_mlock: false, no_extra_bufts: false, flash_attn_type: 'auto', n_gpu_layers: 99});
  expect(records['qwen25-qwen25-1.5b-q80-opencl'].protocol.chatTemplate).toBe('qwen25');
  // They measure the CPU reference prompt (std-pp512-v1, pre-templated); the gate keeps its probes.
  for (const key of ['qwen25-qwen25-1.5b-q80-opencl', 'board-hexagon-llama32-q4km-hexagon',
    'board-litert-qwen25-qwen25-1.5b-q8-litertlm-cpu', 'board-litert-npu-gemma3-1b-sm8850-litertlm-npu']) {
    expect(records[key].protocol).toMatchObject({promptLabel: STANDARD_PROMPT_LABEL, prompt: STANDARD_PROMPT,
      promptFormat: 'raw-pretemplated'});
    expect(records[key].correctness.probes.map(probe => probe.id)).toEqual(records['f8-llama-hexagon'].correctness.probes
      .map(probe => probe.id));
  }
  expect(records['f8-llama-hexagon'].protocol).toMatchObject({promptLabel: 'std-chat-v1',
    promptFormat: 'user-turn-chat-template'});
  expect(records['hashMismatch-npu'].artifact).toEqual({sha256: 'e'.repeat(64), sizeBytes: 600000000,
    expectedSha256: 'fda5dca0e8c1c6f65ca5625c326ff79920c7eb82625a0c6515ae4f5711957b1f'});
  expect(records['f8-litert-npu'].artifact!.expectedSha256).toBe(records['f8-litert-npu'].artifact!.sha256);
  for (const record of Object.values(records)) {
    const keys = Object.keys(record);
    expect(keys.filter(key => !(key in SESSION.properties))).toEqual([]);
    expect(SESSION.required.filter((key: string) => !keys.includes(key))).toEqual([]);
    expect(record.harnessVersion).toBe(SESSION.properties.harnessVersion.const);
  }
  const fixture = {generatedBy: 'xebenchapp/__tests__/accelerator.test.ts', records};
  const text = JSON.stringify(fixture, null, 2) + '\n';
  if (process.env.XEBENCH_RAW_SESSION_OUT) {
    fs.writeFileSync(process.env.XEBENCH_RAW_SESSION_OUT.replace(/(\.json)?$/, '-accelerator.json'), text);
  }
  if (process.env.XEBENCH_UPDATE_FIXTURES === '1') {
    fs.writeFileSync(FIXTURE, text);
  }
  expect(JSON.parse(fs.readFileSync(FIXTURE, 'utf8'))).toEqual(fixture);
});
