import {aggregate, ReferenceSession, runSession} from '../src/harness/session';
import {parseConfig} from '../src/harness/config';
import configData from '../configs/cpu-reference-v1.json';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';

afterEach(() => jest.restoreAllMocks());

const config = parseConfig(configData);
const identity = {runId: 'fixture-run', timestampIso: '2026-01-01T00:00:00Z',
  deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: {sha256: 'a'.repeat(64), sizeBytes: 100}};

function adapter() {
  return {
    engine: 'llama.cpp' as const, backend: 'cpu' as const, modelLabel: 'Llama 3.2 1B',
    quantLabel: 'Q4_K_M', modelFile: config.runs[0].modelFile,
    engineVersion: () => 'fixture-version', settings: () => ({nThreads: 4, nCtx: 2048}),
    load: jest.fn(async () => 17), release: jest.fn(async () => {}),
    benchOnce: jest.fn(async (): Promise<SingleRunMetrics> => ({loadMs: 0, ttftMs: 25,
      promptTokens: 512, decodeTokens: 128, prefillTps: 200, decodeTps: 30,
      prefillMethod: 'engine-timings', decodeMethod: 'engine-timings'})),
  };
}

test('quartiles use documented interpolation and reject non-finite input', () => {
  expect(aggregate([10, 20, 100])).toEqual({median: 20, iqr: 45, min: 10, max: 100, n: 3});
  expect(aggregate([])).toBeNull();
  expect(aggregate([8])).toEqual({median: 8, iqr: 0, min: 8, max: 8, n: 1});
  expect(() => aggregate([NaN])).toThrow();
});

test('retains each repetition and checkpoints before cooldown', async () => {
  const engine = adapter();
  const checkpoints: ReferenceSession[] = [];
  const sleep = jest.fn(async () => {
    expect(checkpoints).toHaveLength(engine.benchOnce.mock.calls.length);
    expect(engine.release).toHaveBeenCalledTimes(engine.benchOnce.mock.calls.length);
  });
  const result = await runSession(engine, config, config.runs[0], identity, async record => {
    checkpoints.push(JSON.parse(JSON.stringify(record)));
  }, sleep);
  expect(checkpoints.map(record => record.status)).toEqual(['running', 'running', 'complete']);
  expect(result.coldRuns.map(run => run.runIndex)).toEqual([0, 1, 2]);
  expect(result.summary.loadMs).toEqual({median: 17, iqr: 0, min: 17, max: 17, n: 3});
  expect(result.coldRuns[0]).toMatchObject({loadMs: 17, promptTokens: 512, decodeTokens: 128});
  expect(result.guardsPassed).toBeNull();
  expect(result.observedBackend).toBeNull();
  expect(sleep.mock.calls).toEqual([[30000], [30000]]);
});

test.each(['load', 'inference'] as const)('retains failed %s attempt and releases resources', async phase => {
  const engine = adapter();
  const action = phase === 'inference' ? engine.benchOnce : engine[phase];
  action.mockRejectedValueOnce(new Error('fixture failure'));
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  const result = await runSession(engine, config, config.runs[0], identity, persist);
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(result.status).toBe('failed');
  expect(result.coldRuns).toHaveLength(1);
  expect(result.coldRuns[0]).toMatchObject({status: 'failed', error: {phase, message: 'fixture failure'}});
  expect(result.summary.decodeTps).toBeNull();
  expect(persist).toHaveBeenCalledTimes(1);
});

test('release failure is persisted before aborting the caller', async () => {
  const engine = adapter();
  engine.release.mockRejectedValueOnce(new Error('fixture cleanup failure'));
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  await expect(runSession(engine, config, config.runs[0], identity, persist)).rejects.toThrow('cleanup');
  expect(persist).toHaveBeenCalledTimes(1);
  expect(persist.mock.calls[0][0]).toMatchObject({status: 'failed', coldRuns: [{
    status: 'failed', error: {phase: 'release'},
    cleanupError: {phase: 'release', message: 'fixture cleanup failure'},
  }]});
  expect(engine.load).toHaveBeenCalledTimes(1);
});

test('zero native decode duration is rejected even with a positive reported throughput', async () => {
  const engine = adapter();
  const metrics = await engine.benchOnce();
  engine.benchOnce.mockResolvedValue({...metrics, engineInternal: {
    promptMs: 10, promptPerSecond: 200, predictedMs: 0, predictedPerSecond: 30,
  }});
  const result = await runSession(engine, config, config.runs[0], identity, async () => {}, async () => {});
  expect(result.status).toBe('failed');
  expect(result.summary.decodeTps).toBeNull();
  expect(engine.release).toHaveBeenCalledTimes(1);
});

test('a later failure preserves successful repetitions and their actual count', async () => {
  const engine = adapter();
  engine.load.mockResolvedValueOnce(17).mockRejectedValueOnce(new Error('second load failed'));
  const result = await runSession(engine, config, config.runs[0], identity, async () => {}, async () => {});
  expect(result.coldRuns).toHaveLength(2);
  expect(result.status).toBe('failed');
  expect(result.summary.decodeTps?.n).toBe(1);
});

test('invalid metrics are not serialized as valid JSON nulls', async () => {
  const engine = adapter();
  const metrics = await engine.benchOnce();
  engine.benchOnce.mockResolvedValue({...metrics, decodeTps: Infinity});
  const result = await runSession(engine, config, config.runs[0], identity, async () => {});
  expect(result.status).toBe('failed');
  expect(result.summary.decodeTps).toBeNull();
});

test('persistence failure stops further native runs after releasing the current engine', async () => {
  const engine = adapter();
  await expect(runSession(engine, config, config.runs[0], identity, async () => {
    throw new Error('disk full');
  })).rejects.toThrow('disk full');
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(engine.load).toHaveBeenCalledTimes(1);
});

test('configuration and effective adapter settings must agree before loading', async () => {
  const engine = adapter();
  engine.settings = () => ({nThreads: 8, nCtx: 2048});
  await expect(runSession(engine, config, config.runs[0], identity, async () => {})).rejects.toThrow('does not match');
  expect(engine.load).not.toHaveBeenCalled();
});

const healthyConditions = {timestampMs: 100, batteryPct: 80, batteryTempC: 30, charging: true,
  powerSaveMode: false, thermalStatus: 0, screenOn: true, foreground: true,
  keyguardLocked: false, pssMb: 100};

test('condition snapshots surround measurement and precede release and persistence', async () => {
  const events: string[] = [];
  jest.spyOn(conditions, 'readConditions').mockImplementation(async () => {
    events.push('probe');
    return conditions.normalizeConditions(healthyConditions);
  });
  const engine = adapter();
  const metrics = await engine.benchOnce();
  engine.load.mockImplementation(async () => {events.push('load'); return 17;});
  engine.benchOnce.mockImplementation(async () => {events.push('inference'); return metrics;});
  engine.release.mockImplementation(async () => {events.push('release');});
  const result = await runSession(engine, {...config, nColdRuns: 1}, config.runs[0], identity,
    async () => {events.push('persist');});
  expect(events).toEqual(['probe', 'load', 'inference', 'probe', 'release', 'persist']);
  expect(result.coldRuns[0].conditions.beforeAssessment.status).toBe('pass');
  expect(result.guardsPassed).toBeNull(); // Endpoint observations are not full protocol validation.
});

test.each(['before', 'after'])('flags failed %s conditions without hiding the measurements', async failedEndpoint => {
  const good = conditions.normalizeConditions(healthyConditions);
  const bad = conditions.normalizeConditions({...healthyConditions, powerSaveMode: true});
  jest.spyOn(conditions, 'readConditions')
    .mockResolvedValueOnce(failedEndpoint === 'before' ? bad : good)
    .mockResolvedValueOnce(failedEndpoint === 'after' ? bad : good);
  const result = await runSession(adapter(), {...config, nColdRuns: 1}, config.runs[0], identity, async () => {});
  expect(result.status).toBe('complete');
  expect(result.guardsPassed).toBe(false);
  expect(result.guardNotes).toContain('power-saving');
  expect(result.summary.decodeTps?.n).toBe(1);
});

test('probe unavailability still saves evidence and releases a failed engine', async () => {
  jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions(null));
  const engine = adapter();
  engine.benchOnce.mockRejectedValueOnce(new Error('inference failure'));
  const result = await runSession(engine, config, config.runs[0], identity, async () => {});
  expect(result.status).toBe('failed');
  expect(result.coldRuns[0].conditions.afterAssessment.status).toBe('unverified');
  expect(result.guardsPassed).toBeNull();
  expect(engine.release).toHaveBeenCalledTimes(1);
});

test.each(['before', 'after'])('a late %s probe aborts measurement and still persists and releases', async endpoint => {
  jest.useFakeTimers();
  try {
    const realRead = conditions.readConditions;
    let pending = true;
    const delayed = () => realRead(() => new Promise(resolve => setTimeout(() => {
      pending = false;
      resolve(healthyConditions);
    }, 2200)));
    const probe = jest.spyOn(conditions, 'readConditions');
    if (endpoint === 'after') probe.mockResolvedValueOnce(conditions.normalizeConditions(healthyConditions));
    probe.mockImplementation(delayed);
    const engine = adapter();
    const persist = jest.fn(async (_record: ReferenceSession) => {});
    const outcome = runSession(engine, config, config.runs[0], identity, persist, async () => {})
      .then(() => 'completed', error => error.message);
    await jest.advanceTimersByTimeAsync(2000);
    expect(pending).toBe(true);
    expect(engine.load).toHaveBeenCalledTimes(endpoint === 'before' ? 0 : 1);
    expect(engine.release).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist.mock.calls[0][0].status).toBe('failed');
    expect(await outcome).toMatch(/probe.*abort/i);
    await jest.advanceTimersByTimeAsync(200);
    expect(pending).toBe(false);
    expect(engine.load).toHaveBeenCalledTimes(endpoint === 'before' ? 0 : 1);
  } finally {
    jest.useRealTimers();
  }
});

test('a still-busy native sampler blocks a later invocation from loading a model', async () => {
  jest.spyOn(conditions, 'readConditions').mockResolvedValue(conditions.normalizeConditions({probeStatus: 'busy'}));
  const engine = adapter();
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  await expect(runSession(engine, config, config.runs[0], identity, persist)).rejects.toThrow('probe');
  expect(engine.load).not.toHaveBeenCalled();
  expect(persist).toHaveBeenCalledTimes(1);
  expect(engine.release).toHaveBeenCalledTimes(1);
});
