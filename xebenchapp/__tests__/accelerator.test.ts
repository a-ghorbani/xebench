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
import {liteRtEngineVersion} from '../src/harness/adapters/LiteRtAdapter';
import type {EngineAdapter} from '../src/harness/adapters/EngineAdapter';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';
import {ORDER_METHOD, seededOrder} from '../src/harness/order';
import f8Config from '../configs/f8-gemma3-accelerators-v1.json';
import s23Config from '../configs/s23-gemma3-accelerators-v1.json';
import bugReproConfig from '../configs/s23-litert-npu-bugrepro-v1.json';

declare const __dirname: string;
declare const process: {env: Record<string, string | undefined>};
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.join(ROOT, 'tests/fixtures/raw-session/app-emitted-accelerator.json');
const SCHEMA = JSON.parse(fs.readFileSync(path.join(ROOT, 'schemas/raw-session-v1.json'), 'utf8'));
const SESSION = SCHEMA.$defs.sessionV3;

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
}

/** An engine adapter that answers gate probes from a table and reports fixed timings. */
function fake(run: AcceleratorRun, options: FakeOptions = {}): EngineAdapter & {loads: number} {
  const answers = {...CORRECT, ...options.answers};
  let measured = 0;
  const adapter = {
    engine: run.engine, backend: run.backend, modelLabel: run.model, quantLabel: run.quant, modelFile: run.modelFile,
    loads: 0,
    engineVersion: () => run.engine === 'litert-lm' ? liteRtEngineVersion(run.backend) :
      `llama.rn 0.0.0-fixture (${run.backend})`,
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
    benchOnce: async (): Promise<SingleRunMetrics> => {
      measured++;
      return {loadMs: 0, ttftMs: 120 + measured, promptTokens: 503, decodeTokens: 96,
        prefillTps: 900 + measured * 3.5, decodeTps: 40 + measured * 0.25,
        prefillMethod: 'engine-timings', decodeMethod: 'engine-timings'};
    },
    backendEvidence: () => {
      const report = run.engine === 'llama.cpp' ? {gpu: options.gpu ?? run.backend !== 'cpu',
        devices: options.devices === undefined ? (run.devices ?? (run.backend === 'gpu' ? ['GPUOpenCL'] : [])) :
          options.devices, androidLib: 'rnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl', reasonNoGpu: null} : null;
      // The first load maps the accelerator runtime; it stays mapped for later loads.
      const markers = run.backend === 'npu' ? ['libLiteRtDispatch_Qualcomm.so', 'libQnnHtp.so'] :
        run.backend === 'gpu' ? ['libOpenCL.so'] : [];
      const libraries = run.engine === 'litert-lm' ? options.libraries ??
        {before: adapter.loads > 1 ? markers : [], after: markers} : null;
      return {status: assessBackend(run.engine, run.backend, {report, libraries}),
        source: report ? 'engine-report' as const : 'process-libraries' as const, report, libraries};
    },
  };
  return adapter;
}

const SHA = (run: AcceleratorRun) => run.modelFile.startsWith('Gemma3') ? 'b'.repeat(64) :
  run.modelFile.endsWith('.gguf') ? 'c'.repeat(64) : 'd'.repeat(64);

async function runConfiguration(config: AcceleratorConfig, options: Record<string, FakeOptions> = {},
  extra: Partial<AcceleratorIO> = {}) {
  const records: Record<string, AcceleratorSession> = {};
  const runs = seededOrder(config.runs, config.orderSeed!);
  const order = runs.map(run => run.id);
  let sequence = 0;
  const outcome = await runAcceleratorConfiguration(config, runs, (_run, position) => ({method: ORDER_METHOD,
    seed: config.orderSeed!, seedSource: 'configured', order, position}), {
    artifact: async run => ({sha256: SHA(run), sizeBytes: 600000000}),
    adapter: run => fake(run, options[run.id]),
    identity: () => ({runId: `1767225600000-${sequence++}-accel`, timestampIso: '2026-01-01T00:00:00.000Z',
      deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: null}),
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
  // The parsed configuration is exactly what is echoed into records.
  expect(f8).toEqual(f8Config);
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

  const run = bug.runs[1];
  out['setupFailed-npu'] = unmeasuredAcceleratorSession(fake(run), small(bug), run,
    {runId: '1767225600000-99-accel', timestampIso: '2026-01-01T00:00:00.000Z',
      deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: null}, null, 'setup-failed',
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
  });
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
