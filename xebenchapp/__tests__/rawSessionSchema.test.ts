/**
 * Keeps the app's ReferenceSession output and schemas/raw-session-v1.json in step.
 * Records produced here by runSession are compared with the committed fixture that
 * tests/test_raw_session.py validates with the host validator. After an intended
 * record change, regenerate with XEBENCH_UPDATE_FIXTURES=1 and update the schema.
 * XEBENCH_RAW_SESSION_OUT=<file> also writes the live records for CI validation.
 * tests/fixtures/raw-session/app-emitted-v1.json is the frozen output of the
 * earlier cpu-reference-v1 app and must keep validating unchanged.
 */
import {ReferenceSession, runSession, SessionContext, unmeasuredSession} from '../src/harness/session';
import {parseConfig} from '../src/harness/config';
import configData from '../configs/cpu-reference-v1.json';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';
import {ORDER_METHOD, seededOrder} from '../src/harness/order';

// Node globals for this Jest-only file; the app's tsconfig has no Node types.
declare const __dirname: string;
declare const process: {env: Record<string, string | undefined>};
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.join(ROOT, 'tests/fixtures/raw-session/app-emitted.json');
const SCHEMA = JSON.parse(fs.readFileSync(path.join(ROOT, 'schemas/raw-session-v1.json'), 'utf8'));
const SESSION = SCHEMA.$defs.sessionV2;

afterEach(() => jest.restoreAllMocks());

const config = parseConfig(configData);
const identity = {runId: '1767225600000-0-fixture', timestampIso: '2026-01-01T00:00:00.000Z',
  deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: {sha256: 'a'.repeat(64), sizeBytes: 770000000}};
// USB-tethered at a charge limit (charging-policy-v2 eligible).
const healthy = {timestampMs: 1767225600000, elapsedMs: 5000, batteryPct: 80, batteryTempC: 30.5, charging: true,
  batteryStatus: 4, plugged: 2, currentNowUa: -1500,
  powerSaveMode: false, thermalStatus: 0, thermalHeadroom: 0.4, screenOn: true, foreground: true,
  keyguardLocked: false, focusLossCount: 0, processUptimeMs: 60000, rssMb: 1800.5, pssMb: 412.25};
const monitor = (samples: object[] = [healthy, {...healthy, elapsedMs: 6000}], extra: object = {}) => ({
  status: 'stopped', intervalMs: 1000, startedElapsedMs: 5000, stoppedElapsedMs: 6500, ticks: samples.length,
  dropped: 0, expired: false, maxSampleCostMs: 2.5, totalSampleCostMs: 4, samples, ...extra});
const noSleep = async () => {};
const context = (extra: SessionContext = {}): SessionContext => ({now: () => 0, ...extra});

// Varied values exercise interpolated medians/IQRs with an even sample count.
const samples = [
  {loadMs: 17, ttftMs: 25.5, prefillTps: 200.25, decodeTps: 30.125},
  {loadMs: 19, ttftMs: 27, prefillTps: 190.5, decodeTps: 31.5},
  {loadMs: 18, ttftMs: 26.25, prefillTps: 210.75, decodeTps: 29.25},
  {loadMs: 23, ttftMs: 24, prefillTps: 205, decodeTps: 30.75},
];

function adapter() {
  let call = 0;
  return {
    engine: 'llama.cpp' as const, backend: 'cpu' as const, modelLabel: config.runs[0].model,
    quantLabel: config.runs[0].quant, modelFile: config.runs[0].modelFile,
    engineVersion: () => 'llama.rn 0.0.0-fixture', settings: () => ({nThreads: 4, nCtx: 2048}),
    load: jest.fn(async () => samples[call % samples.length].loadMs),
    release: jest.fn(async () => {}),
    benchOnce: jest.fn(async (): Promise<SingleRunMetrics> => {
      const sample = samples[call++ % samples.length];
      return {loadMs: 0, ttftMs: sample.ttftMs, promptTokens: 512, decodeTokens: 127,
        prefillTps: sample.prefillTps, decodeTps: sample.decodeTps,
        prefillMethod: 'engine-timings', decodeMethod: 'engine-timings',
        engineInternal: {promptMs: 512000 / sample.prefillTps, promptPerSecond: sample.prefillTps,
          predictedMs: 127000 / sample.decodeTps, predictedPerSecond: sample.decodeTps}};
    }),
  };
}

async function capture(run: (persist: (record: ReferenceSession) => Promise<void>) => Promise<unknown>) {
  const written: ReferenceSession[] = [];
  // The app persists JSON.stringify(record); compare exactly those bytes' meaning.
  await run(async record => {written.push(JSON.parse(JSON.stringify(record)));}).catch(() => {});
  return written;
}

/** Healthy native responses unless a case overrides them. */
function native(read: object = healthy, stop: object = monitor(), start: Awaited<ReturnType<typeof conditions.startMonitor>> = 'started') {
  jest.restoreAllMocks();
  const probe = jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions(read));
  jest.spyOn(conditions, 'startMonitor').mockResolvedValue(start);
  jest.spyOn(conditions, 'stopMonitor').mockResolvedValue(conditions.normalizeMonitor(stop));
  return probe;
}

async function emitted(): Promise<Record<string, ReferenceSession>> {
  const records: Record<string, ReferenceSession> = {};
  const one = {...config, nColdRuns: 1};
  const single = async (engine: ReturnType<typeof adapter>, cfg = one, extra: SessionContext = {}, run = config.runs[0]) =>
    (await capture(persist => runSession(engine, cfg, run, identity, persist, noSleep, context(extra)))).pop()!;

  native();
  const four = {...config, nColdRuns: 4};
  const order = {method: ORDER_METHOD, seed: 7, seedSource: 'generated' as const, order: [config.runs[0].id], position: 0};
  const complete = await capture(persist => runSession(adapter(), four, config.runs[0], identity, persist, noSleep,
    context({runOrder: order})));
  records.partial = complete[0];
  records.complete = complete[complete.length - 1];

  const failing = adapter();
  failing.load.mockResolvedValueOnce(17).mockRejectedValueOnce(new Error('fixture load failure'));
  records.failed = await single(failing, config);

  const leaking = adapter();
  leaking.release.mockRejectedValueOnce(new Error('fixture cleanup failure'));
  records.cleanupFailed = await single(leaking, config);

  // The after endpoint and a sample are record-and-flag; the ready endpoint cannot fail.
  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthy, powerSaveMode: true}));
  jest.mocked(conditions.stopMonitor).mockResolvedValueOnce(conditions.normalizeMonitor(
    monitor([healthy, {...healthy, elapsedMs: 6000, thermalStatus: 3}])));
  records.flagged = await single(adapter(), {...config, nColdRuns: 2});

  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthy, focusLossCount: 1}))
    .mockResolvedValue(conditions.normalizeConditions({...healthy, focusLossCount: 1}));
  records.interrupted = await single(adapter(), {...config, nColdRuns: 2});

  native(healthy, monitor([healthy], {expired: true}));
  records.monitorIncomplete = await single(adapter());

  native(healthy, monitor(), 'unavailable');
  records.monitorUnavailable = await single(adapter());

  native(healthy, {status: 'stuck'});
  records.monitorStuck = await single(adapter());

  native(healthy, monitor(), 'busy');
  records.monitorBusy = await single(adapter());

  native({...healthy, thermalStatus: 1});
  records.readinessTimeout = await single(adapter(), {...config, readinessTimeoutSec: 10});

  // Without the native module, every condition reading is unknown: never ready.
  jest.restoreAllMocks();
  records.probeUnavailable = await single(adapter(), {...one, readinessTimeoutSec: 10});

  native();
  const inference = adapter();
  // Native messages are unbounded; a long one must remain valid evidence.
  inference.benchOnce.mockRejectedValueOnce(new Error('fixture inference failure '.repeat(200)));
  records.inferenceFailed = await single(inference);

  const both = adapter();
  both.load.mockRejectedValueOnce('fixture non-Error load failure');
  both.release.mockRejectedValueOnce(new Error('fixture cleanup failure'));
  records.loadAndCleanupFailed = await single(both);

  const invalid = adapter();
  const metrics = await invalid.benchOnce();
  const withoutInternal = {...metrics};
  delete withoutInternal.engineInternal;
  invalid.benchOnce.mockResolvedValueOnce({...withoutInternal, decodeTps: NaN});
  records.nonFiniteMetrics = await single(invalid);

  const plain = adapter();
  plain.benchOnce.mockResolvedValueOnce(withoutInternal);
  records.noEngineInternal = await single(plain);

  native({probeStatus: 'busy'});
  records.beforeProbeBusy = await single(adapter());

  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValue(conditions.normalizeConditions({probeStatus: 'timeout'}));
  records.afterProbeTimeout = await single(adapter());

  // charging-policy-v2: active charging during a run, until a readiness timeout, and unreadable.
  native(healthy, monitor([healthy, {...healthy, elapsedMs: 6000, batteryStatus: 2}]));
  records.chargingDuringRun = await single(adapter());
  native({...healthy, batteryStatus: 2});
  records.chargingTimeout = await single(adapter(), {...one, readinessTimeoutSec: 10});
  const unreadable = {...healthy, batteryStatus: null, currentNowUa: null};
  native(unreadable, monitor([unreadable, {...unreadable, elapsedMs: 6000}]));
  records.chargingUnknown = await single(adapter());

  // A later run in a seeded two-run configuration, with a shared readiness baseline.
  native();
  const runs = [config.runs[0], {...config.runs[0], id: 'second-run'}];
  const twoRuns = {...config, nColdRuns: 2, orderSeed: 12345, runs};
  const ids = seededOrder(runs.map(run => run.id), 12345);
  // The session at position 1, i.e. the run executed second.
  const later = runs.find(run => run.id === ids[1])!;
  const runOrder = {method: ORDER_METHOD, seed: 12345, seedSource: 'configured' as const, order: ids, position: 1};
  records.orderedLaterRun = await single(adapter(), twoRuns,
    {runOrder, baseline: {batteryTempC: 30}, initialCooldown: true}, later);
  records.configurationAborted = unmeasuredSession(adapter(), twoRuns, later, {...identity, artifact: null},
    runOrder, 'configuration-aborted');

  // Further app paths: failure then a pending after-probe, a late monitor start,
  // cleanup failure with a pending probe, a cooled-baseline timeout after a success,
  // the null-headroom confirmation, a failed stop after success and decimated samples.
  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValue(conditions.normalizeConditions({probeStatus: 'timeout'}));
  const failsThenPending = adapter();
  failsThenPending.benchOnce.mockRejectedValueOnce(new Error('fixture inference failure'));
  records.inferenceFailedThenProbePending = await single(failsThenPending);
  native(healthy, monitor(), 'timeout');
  records.monitorStartTimeout = await single(adapter());
  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValue(conditions.normalizeConditions({probeStatus: 'busy'}));
  const leakingPending = adapter();
  leakingPending.release.mockRejectedValueOnce(new Error('fixture cleanup failure'));
  records.cleanupFailedProbePending = await single(leakingPending);
  native().mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValue(conditions.normalizeConditions({...healthy, batteryTempC: 33}));
  records.laterReadinessTimeout = await single(adapter(), {...config, readinessTimeoutSec: 10});
  native().mockResolvedValueOnce(conditions.normalizeConditions({...healthy, thermalHeadroom: null}))
    .mockResolvedValue(conditions.normalizeConditions({...healthy, thermalHeadroom: null}));
  records.headroomUnavailable = await single(adapter());
  native(healthy, {status: 'stopped'});
  jest.mocked(conditions.stopMonitor).mockResolvedValueOnce(conditions.normalizeMonitor(null, 'error'));
  records.monitorStopError = await single(adapter());
  const many = Array.from({length: 45}, (_, index) => ({...healthy, elapsedMs: 5000 + index * 1000,
    batteryTempC: index === 17 ? 31.5 : 30.5}));
  native(healthy, monitor(many, {stoppedElapsedMs: 49500}));
  records.decimatedSamples = await single(adapter());
  records.setupFailed = unmeasuredSession(adapter(), one, config.runs[0], identity, null, 'setup-failed');
  // The per-process ID is random by design; pin it so the fixture is reproducible.
  for (const record of Object.values(records)) {
    for (const attempt of record.coldRuns) {
      if (attempt.instance) attempt.instance.processInstanceId = 'fixture-process';
    }
  }
  return JSON.parse(JSON.stringify(records));
}

test('runSession records match the committed fixture validated by the host schema', async () => {
  const records = await emitted();
  expect(Object.fromEntries(Object.entries(records).map(([key, value]) =>
    [key, `${value.status}/${value.stopReason}/${value.conditionVerdict.status}`]))).toEqual({
    partial: 'running/null/unverified', complete: 'complete/null/pass', failed: 'failed/repetition-failed/unverified',
    cleanupFailed: 'failed/cleanup-failed/unverified', flagged: 'complete/null/fail',
    interrupted: 'complete/null/fail', monitorIncomplete: 'complete/null/unverified',
    monitorUnavailable: 'complete/null/unverified', monitorStuck: 'failed/condition-probe-pending/fail',
    monitorBusy: 'failed/condition-probe-pending/fail', readinessTimeout: 'failed/readiness-timeout/fail',
    probeUnavailable: 'failed/readiness-timeout/unverified', inferenceFailed: 'failed/repetition-failed/unverified',
    loadAndCleanupFailed: 'failed/cleanup-failed/unverified', nonFiniteMetrics: 'failed/repetition-failed/unverified',
    noEngineInternal: 'complete/null/pass', beforeProbeBusy: 'failed/condition-probe-pending/fail',
    afterProbeTimeout: 'failed/condition-probe-pending/fail', orderedLaterRun: 'complete/null/pass',
    configurationAborted: 'failed/configuration-aborted/unverified', setupFailed: 'failed/setup-failed/unverified',
    chargingDuringRun: 'complete/null/pass', chargingTimeout: 'failed/readiness-timeout/unverified',
    chargingUnknown: 'complete/null/pass', inferenceFailedThenProbePending: 'failed/condition-probe-pending/fail',
    monitorStartTimeout: 'failed/condition-probe-pending/fail', cleanupFailedProbePending: 'failed/cleanup-failed/fail',
    laterReadinessTimeout: 'failed/readiness-timeout/fail', headroomUnavailable: 'complete/null/pass',
    monitorStopError: 'failed/condition-probe-pending/fail', decimatedSamples: 'complete/null/pass'});
  expect(['complete', 'chargingDuringRun', 'chargingTimeout', 'chargingUnknown'].map(key =>
    records[key].conditionVerdict.charging)).toEqual(['satisfied', 'violated', 'violated', 'unknown']);
  for (const record of Object.values(records)) {
    const keys = Object.keys(record);
    expect(keys.filter(key => !(key in SESSION.properties))).toEqual([]);
    expect(SESSION.required.filter((key: string) => !keys.includes(key))).toEqual([]);
    expect(record.schemaVersion).toBe(SESSION.properties.schemaVersion.const);
    expect(record.harnessVersion).toBe(SESSION.properties.harnessVersion.const);
  }
  const fixture = {generatedBy: 'xebenchapp/__tests__/rawSessionSchema.test.ts', records};
  const text = JSON.stringify(fixture, null, 2) + '\n';
  if (process.env.XEBENCH_RAW_SESSION_OUT) {
    fs.writeFileSync(process.env.XEBENCH_RAW_SESSION_OUT, text);
  }
  if (process.env.XEBENCH_UPDATE_FIXTURES === '1') {
    fs.mkdirSync(path.dirname(FIXTURE), {recursive: true});
    fs.writeFileSync(FIXTURE, text);
  }
  expect(JSON.parse(fs.readFileSync(FIXTURE, 'utf8'))).toEqual(fixture);
});
