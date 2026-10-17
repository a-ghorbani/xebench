/**
 * Keeps the app's ReferenceSession output and schemas/raw-session-v1.json in step.
 * Records produced here by runSession are compared with the committed fixture that
 * tests/test_raw_session.py validates with the host validator. After an intended
 * record change, regenerate with XEBENCH_UPDATE_FIXTURES=1 and update the schema.
 * XEBENCH_RAW_SESSION_OUT=<file> also writes the live records for CI validation.
 */
import {ReferenceSession, runSession} from '../src/harness/session';
import {parseConfig} from '../src/harness/config';
import configData from '../configs/cpu-reference-v1.json';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';

// Node globals for this Jest-only file; the app's tsconfig has no Node types.
declare const __dirname: string;
declare const process: {env: Record<string, string | undefined>};
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.join(ROOT, 'tests/fixtures/raw-session/app-emitted.json');
const SCHEMA = JSON.parse(fs.readFileSync(path.join(ROOT, 'schemas/raw-session-v1.json'), 'utf8'));

afterEach(() => jest.restoreAllMocks());

const config = parseConfig(configData);
const identity = {runId: '1767225600000-0-fixture', timestampIso: '2026-01-01T00:00:00.000Z',
  deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: {sha256: 'a'.repeat(64), sizeBytes: 770000000}};
const healthy = {timestampMs: 1767225600000, batteryPct: 80, batteryTempC: 30.5, charging: false,
  powerSaveMode: false, thermalStatus: 0, screenOn: true, foreground: true, keyguardLocked: false, pssMb: 412.25};

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

async function emitted(): Promise<Record<string, ReferenceSession>> {
  const records: Record<string, ReferenceSession> = {};
  jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions(healthy));
  const four = {...config, nColdRuns: 4};
  const complete = await capture(persist => runSession(adapter(), four, config.runs[0], identity, persist, async () => {}));
  records.partial = complete[0];
  records.complete = complete[complete.length - 1];

  const failing = adapter();
  failing.load.mockResolvedValueOnce(17).mockRejectedValueOnce(new Error('fixture load failure'));
  records.failed = (await capture(persist => runSession(failing, config, config.runs[0], identity, persist, async () => {}))).pop()!;

  const leaking = adapter();
  leaking.release.mockRejectedValueOnce(new Error('fixture cleanup failure'));
  records.cleanupFailed = (await capture(persist => runSession(leaking, config, config.runs[0], identity, persist))).pop()!;

  jest.restoreAllMocks();
  const flagged = {...healthy, powerSaveMode: true};
  jest.spyOn(conditions, 'readConditions').mockResolvedValueOnce(conditions.normalizeConditions(flagged))
    .mockResolvedValue(conditions.normalizeConditions(healthy));
  records.flagged = (await capture(persist => runSession(adapter(), {...config, nColdRuns: 2}, config.runs[0],
    identity, persist, async () => {}))).pop()!;

  // Without the native module, every condition reading is unknown.
  jest.restoreAllMocks();
  const one = {...config, nColdRuns: 1};
  const single = async (engine: ReturnType<typeof adapter>) =>
    (await capture(persist => runSession(engine, one, config.runs[0], identity, persist))).pop()!;
  records.probeUnavailable = await single(adapter());

  jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions(healthy));
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

  jest.restoreAllMocks();
  jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions({probeStatus: 'busy'}));
  records.beforeProbeBusy = await single(adapter());

  jest.restoreAllMocks();
  jest.spyOn(conditions, 'readConditions').mockResolvedValueOnce(conditions.normalizeConditions(healthy))
    .mockResolvedValue(conditions.normalizeConditions({probeStatus: 'timeout'}));
  records.afterProbeTimeout = await single(adapter());
  return records;
}

test('runSession records match the committed fixture validated by the host schema', async () => {
  const records = await emitted();
  expect(Object.fromEntries(Object.entries(records).map(([key, value]) => [key, value.status]))).toEqual({
    partial: 'running', complete: 'complete', failed: 'failed', cleanupFailed: 'failed',
    flagged: 'complete', probeUnavailable: 'complete', inferenceFailed: 'failed',
    loadAndCleanupFailed: 'failed', nonFiniteMetrics: 'failed', noEngineInternal: 'complete',
    beforeProbeBusy: 'failed', afterProbeTimeout: 'failed'});
  for (const record of Object.values(records)) {
    const keys = Object.keys(record);
    expect(keys.filter(key => !(key in SCHEMA.properties))).toEqual([]);
    expect(SCHEMA.required.filter((key: string) => !keys.includes(key))).toEqual([]);
    expect(record.schemaVersion).toBe(SCHEMA.properties.schemaVersion.const);
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
