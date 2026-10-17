import {aggregate, PROCESS_INSTANCE_ID, ReferenceSession, runSession} from '../src/harness/session';
import {parseConfig} from '../src/harness/config';
import configData from '../configs/cpu-reference-v1.json';
import type {SingleRunMetrics} from '../src/harness/types';
import * as conditions from '../src/harness/conditions';

const healthyConditions = {timestampMs: 100, elapsedMs: 1000, batteryPct: 80, batteryTempC: 30, charging: true,
  batteryStatus: 5, plugged: 2, currentNowUa: 0,
  powerSaveMode: false, thermalStatus: 0, thermalHeadroom: 0.3, screenOn: true, foreground: true,
  keyguardLocked: false, focusLossCount: 0, processUptimeMs: 5000, rssMb: 200, pssMb: 100};
const stoppedMonitor = (samples: object[] = [healthyConditions], extra: object = {}) => conditions.normalizeMonitor({
  status: 'stopped', intervalMs: 1000, startedElapsedMs: 1000, stoppedElapsedMs: 1500,
  ticks: samples.length, dropped: 0, expired: false,
  maxSampleCostMs: 1, totalSampleCostMs: 1, samples, ...extra});

// Healthy measured evidence by default; individual tests override specific native responses.
beforeEach(() => {
  jest.spyOn(conditions, 'readConditions').mockImplementation(async () => conditions.normalizeConditions(healthyConditions));
  jest.spyOn(conditions, 'startMonitor').mockResolvedValue('started');
  jest.spyOn(conditions, 'stopMonitor').mockImplementation(async () => stoppedMonitor());
});
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


const one = {...config, nColdRuns: 1};
const realRead = conditions.readConditions;
const noSleep = async () => {};

test('readiness, sampling and endpoints surround measurement and precede release and persistence', async () => {
  const events: string[] = [];
  jest.mocked(conditions.readConditions).mockImplementation(async () => {
    events.push('probe');
    return conditions.normalizeConditions(healthyConditions);
  });
  jest.mocked(conditions.startMonitor).mockImplementation(async () => {events.push('monitor-start'); return 'started';});
  jest.mocked(conditions.stopMonitor).mockImplementation(async () => {events.push('monitor-stop'); return stoppedMonitor();});
  const engine = adapter();
  const metrics = await engine.benchOnce();
  engine.load.mockImplementation(async () => {events.push('load'); return 17;});
  engine.benchOnce.mockImplementation(async () => {events.push('inference'); return metrics;});
  engine.release.mockImplementation(async () => {events.push('release');});
  const result = await runSession(engine, one, config.runs[0], identity, async () => {events.push('persist');});
  // No JS-bridge sampling call happens between the start of load and the end of inference.
  expect(events).toEqual(['probe', 'monitor-start', 'load', 'inference', 'monitor-stop', 'probe', 'release', 'persist']);
  const attempt = result.coldRuns[0];
  expect(attempt.readiness).toMatchObject({status: 'ready', polls: 1, waitedMs: expect.any(Number)});
  expect(attempt.conditions.duringAssessment).toEqual({status: 'pass', reasons: []});
  expect(result.conditionVerdict).toEqual({status: 'pass', reasons: [], charging: 'satisfied'});
  // A condition pass is not full guard validation: backend execution remains unverified.
  expect(result.guardsPassed).toBeNull();
  expect(result.guardNotes).toEqual(['Actual backend execution has not been verified']);
});

// The before endpoint is the ready poll, so it cannot fail; samples and the after endpoint are record-and-flag.
test.each(['during', 'after'])('flags failed %s conditions without hiding the measurements', async failed => {
  const bad = {...healthyConditions, thermalStatus: 3};
  if (failed === 'during') jest.mocked(conditions.stopMonitor).mockResolvedValueOnce(stoppedMonitor([healthyConditions, bad]));
  if (failed === 'after') {
    jest.mocked(conditions.readConditions)
      .mockResolvedValueOnce(conditions.normalizeConditions(healthyConditions))
      .mockResolvedValueOnce(conditions.normalizeConditions(bad));
  }
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
  expect(result.status).toBe('complete');
  expect(result.guardsPassed).toBe(false);
  expect(result.conditionVerdict).toMatchObject({status: 'fail', reasons: ['severe-thermal-state']});
  expect(result.guardNotes).toContain('severe-thermal-state');
  expect(result.summary.decodeTps?.n).toBe(1);
});

test('an interruption is flagged, then the next repetition waits for the app to regain focus', async () => {
  const lost = {...healthyConditions, focusLossCount: 1};
  jest.mocked(conditions.readConditions)
    .mockResolvedValueOnce(conditions.normalizeConditions(healthyConditions)) // ready
    .mockResolvedValueOnce(conditions.normalizeConditions(lost)) // after: focus was lost meanwhile
    .mockResolvedValueOnce(conditions.normalizeConditions({...lost, foreground: false})) // still away
    .mockImplementation(async () => conditions.normalizeConditions(lost)); // back in front
  const sleep = jest.fn(noSleep);
  const result = await runSession(adapter(), {...config, nColdRuns: 2}, config.runs[0], identity, async () => {}, sleep);
  expect(result.coldRuns[0].conditions.duringAssessment).toEqual({status: 'fail', reasons: ['interrupted']});
  expect(result.coldRuns[1].readiness).toMatchObject({status: 'ready', polls: 2, notReady: {'not-foreground': 1}});
  expect(result.coldRuns[1].conditions.duringAssessment?.status).toBe('pass');
  expect(sleep.mock.calls).toEqual([[30000], [5000]]);
  expect(result.status).toBe('complete');
  expect(result.conditionVerdict).toMatchObject({status: 'fail', reasons: ['interrupted']});
});

test('readiness timeout skips the repetition without loading and records it as a failed condition', async () => {
  jest.mocked(conditions.readConditions).mockImplementation(async () =>
    conditions.normalizeConditions({...healthyConditions, thermalStatus: 1}));
  const engine = adapter();
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  const sleep = jest.fn(noSleep);
  const result = await runSession(engine, {...config, readinessTimeoutSec: 10}, config.runs[0], identity, persist, sleep);
  expect(engine.load).not.toHaveBeenCalled();
  expect(conditions.startMonitor).not.toHaveBeenCalled();
  expect(sleep.mock.calls).toEqual([[5000], [5000]]);
  expect(result.coldRuns).toEqual([expect.objectContaining({status: 'skipped', reason: 'readiness-timeout',
    instance: null, readiness: expect.objectContaining({status: 'timeout', polls: 3, reasons: ['thermal-not-ready']})})]);
  expect(result).toMatchObject({status: 'failed', stopReason: 'readiness-timeout', repetitionsPlanned: 3,
    guardsPassed: false, conditionVerdict: {status: 'fail', reasons: ['readiness-timeout']}});
  expect(persist).toHaveBeenCalledTimes(1);
});

test('unavailable probes never satisfy readiness, so nothing is measured', async () => {
  jest.mocked(conditions.readConditions).mockImplementation(async () => conditions.normalizeConditions(null));
  const engine = adapter();
  const result = await runSession(engine, config, config.runs[0], identity, async () => {}, noSleep);
  expect(engine.load).not.toHaveBeenCalled();
  expect(result.coldRuns[0]).toMatchObject({status: 'skipped', readiness: {status: 'timeout', polls: 37}});
  expect(result.coldRuns[0].conditions.beforeAssessment.status).toBe('unverified');
  // Unknown evidence is neither a pass nor an observed failure.
  expect(result).toMatchObject({status: 'failed', stopReason: 'readiness-timeout', guardsPassed: null,
    conditionVerdict: {status: 'unverified', reasons: []}});
});

test('incomplete during-run coverage keeps the verdict unverified, never pass', async () => {
  jest.mocked(conditions.stopMonitor).mockResolvedValue(stoppedMonitor([healthyConditions], {expired: true}));
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
  expect(result.status).toBe('complete');
  expect(result.conditionVerdict.status).toBe('unverified');
  expect(result.guardsPassed).toBeNull();
  expect(result.guardNotes).toContain('Run conditions not fully verified');
});

test('a missing monitor is recorded as unavailable and cannot pass', async () => {
  jest.mocked(conditions.startMonitor).mockResolvedValue('unavailable');
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
  expect(conditions.stopMonitor).not.toHaveBeenCalled();
  expect(result.coldRuns[0].conditions.during?.status).toBe('unavailable');
  expect(result.conditionVerdict.status).toBe('unverified');
});

test('the monitor is stopped and the engine released when inference fails', async () => {
  const engine = adapter();
  engine.benchOnce.mockRejectedValueOnce(new Error('inference failure'));
  const result = await runSession(engine, config, config.runs[0], identity, async () => {});
  expect(conditions.stopMonitor).toHaveBeenCalledTimes(1);
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(result).toMatchObject({status: 'failed', stopReason: 'repetition-failed'});
  expect(result.conditionVerdict.status).toBe('unverified');
});

test.each(['busy', 'timeout'] as const)('a %s monitor start aborts before loading and still persists', async status => {
  jest.mocked(conditions.startMonitor).mockResolvedValue(status);
  const engine = adapter();
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  await expect(runSession(engine, config, config.runs[0], identity, persist)).rejects.toThrow(/abort/);
  expect(engine.load).not.toHaveBeenCalled();
  // A late-started monitor gets one bounded stop attempt; a busy one belongs to earlier work.
  expect(conditions.stopMonitor).toHaveBeenCalledTimes(status === 'timeout' ? 1 : 0);
  expect(persist).toHaveBeenCalledTimes(1);
  expect(persist.mock.calls[0][0]).toMatchObject({status: 'failed', stopReason: 'condition-probe-pending',
    guardsPassed: false, conditionVerdict: {status: 'fail', reasons: ['condition-probe-pending']}});
});

test.each(['stuck', 'timeout', 'error'] as const)('a %s monitor stop aborts after release and persistence', async status => {
  jest.mocked(conditions.stopMonitor).mockResolvedValue(conditions.normalizeMonitor(null, status));
  const engine = adapter();
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  await expect(runSession(engine, config, config.runs[0], identity, persist, noSleep)).rejects.toThrow(/abort/);
  expect(engine.load).toHaveBeenCalledTimes(1);
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(persist.mock.calls[0][0]).toMatchObject({status: 'failed', stopReason: 'condition-probe-pending',
    coldRuns: [{status: 'failed', error: {phase: 'conditions'}, conditions: {after: null}}],
    conditionVerdict: {status: 'fail', reasons: ['condition-probe-pending']}});
  // No endpoint probe runs beside a monitor that may still be sampling.
  expect(conditions.readConditions).toHaveBeenCalledTimes(1);
});

test('records fresh-instance identity, process loads, run order and the charging policy', async () => {
  jest.mocked(conditions.readConditions).mockImplementation(async () =>
    conditions.normalizeConditions({...healthyConditions, batteryStatus: 4}));
  const runOrder = {method: 'mulberry32-fisher-yates-v1' as const, seed: 7, seedSource: 'configured' as const,
    order: ['b', config.runs[0].id], position: 1};
  const sleep = jest.fn(noSleep);
  const result = await runSession(adapter(), {...config, nColdRuns: 2}, config.runs[0], identity, async () => {},
    sleep, {runOrder, initialCooldown: true});
  expect(sleep.mock.calls).toEqual([[30000], [30000]]);
  expect(result.coldRuns.map(run => run.cooldownMs)).toEqual([30000, 30000]);
  const [first, second] = result.coldRuns.map(run => run.instance!);
  expect(first).toMatchObject({kind: 'fresh-instance', request: 'first-request-on-instance',
    processInstanceId: PROCESS_INSTANCE_ID});
  expect(second.modelLoadsBeforeInProcess).toBe(first.modelLoadsBeforeInProcess + 1);
  expect(result.runOrder).toEqual(runOrder);
  expect(result.protocol).toMatchObject({measurementKind: 'fresh-instance-first-request',
    warmRequests: 'not-measured', processRestartBetweenRepetitions: false, readinessTimeoutSec: 180});
  expect(result.conditionVerdict).toEqual({status: 'pass', reasons: [], charging: 'satisfied'});
  expect(result.chargingPolicy).toMatchObject({version: 'charging-policy-v3', externalPower: 'allowed'});
});

test('the readiness temperature baseline is shared across runs of a configuration', async () => {
  const baseline = {batteryTempC: 27.5};
  jest.mocked(conditions.readConditions).mockImplementation(async () => conditions.normalizeConditions(healthyConditions));
  const result = await runSession(adapter(), {...one, readinessTimeoutSec: 10}, config.runs[0], identity,
    async () => {}, noSleep, {baseline});
  expect(result.coldRuns[0]).toMatchObject({status: 'skipped', readiness: {baselineBatteryTempC: 27.5,
    reasons: ['battery-not-cooled']}});
});

test.each(['before', 'after'])('a late %s probe aborts measurement and still persists and releases', async endpoint => {
  jest.useFakeTimers();
  try {
    let pending = true;
    const delayed = () => realRead(() => new Promise(resolve => setTimeout(() => {
      pending = false;
      resolve(healthyConditions);
    }, 2200)));
    const probe = jest.mocked(conditions.readConditions);
    if (endpoint === 'after') probe.mockResolvedValueOnce(conditions.normalizeConditions(healthyConditions));
    probe.mockImplementation(delayed);
    const engine = adapter();
    const persist = jest.fn(async (_record: ReferenceSession) => {});
    const outcome = runSession(engine, config, config.runs[0], identity, persist, noSleep)
      .then(() => 'completed', error => error.message);
    await jest.advanceTimersByTimeAsync(2000);
    expect(pending).toBe(true);
    expect(engine.load).toHaveBeenCalledTimes(endpoint === 'before' ? 0 : 1);
    expect(engine.release).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist.mock.calls[0][0]).toMatchObject({status: 'failed',
      conditionVerdict: {status: 'fail', reasons: ['condition-probe-pending']}});
    expect(await outcome).toMatch(/probe.*abort/i);
    await jest.advanceTimersByTimeAsync(200);
    expect(pending).toBe(false);
    expect(engine.load).toHaveBeenCalledTimes(endpoint === 'before' ? 0 : 1);
  } finally {
    jest.useRealTimers();
  }
});

test('a still-busy native sampler blocks a later invocation from loading a model', async () => {
  jest.mocked(conditions.readConditions).mockResolvedValue(conditions.normalizeConditions({probeStatus: 'busy'}));
  const engine = adapter();
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  await expect(runSession(engine, config, config.runs[0], identity, persist)).rejects.toThrow('probe');
  expect(engine.load).not.toHaveBeenCalled();
  expect(persist).toHaveBeenCalledTimes(1);
  expect(persist.mock.calls[0][0]).toMatchObject({stopReason: 'condition-probe-pending',
    coldRuns: [{status: 'failed', readiness: {status: 'probe-pending'}}]});
  expect(engine.release).toHaveBeenCalledTimes(1);
});

test('a null headroom reading gets one confirmation poll before readiness accepts it', async () => {
  jest.mocked(conditions.readConditions)
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthyConditions, thermalHeadroom: null}))
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthyConditions, thermalHeadroom: null}));
  const sleep = jest.fn(noSleep);
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {}, sleep);
  expect(sleep.mock.calls).toEqual([[1500]]);
  expect(result.coldRuns[0].readiness).toMatchObject({status: 'ready', polls: 2, headroomApplied: false});
});

test('a headroom reading on confirmation is applied to readiness', async () => {
  jest.mocked(conditions.readConditions)
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthyConditions, thermalHeadroom: null}))
    .mockResolvedValueOnce(conditions.normalizeConditions({...healthyConditions, thermalHeadroom: 0.95}))
    .mockImplementation(async () => conditions.normalizeConditions(healthyConditions));
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {}, noSleep);
  expect(result.coldRuns[0].readiness).toMatchObject({status: 'ready', polls: 3, headroomApplied: true,
    notReady: {'thermal-headroom-low': 1}});
});

test('charging cannot be satisfied when the run itself was not sampled', async () => {
  jest.mocked(conditions.startMonitor).mockResolvedValue('unavailable');
  const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
  expect(result.conditionVerdict.charging).toBe('unknown');
});

test('a configured rise bound overrides the default and is recorded in the protocol', async () => {
  const result = await runSession(adapter(), {...one, readinessTimeoutSec: 10, maxBatteryTempRiseC: 0.5}, config.runs[0],
    identity, async () => {}, noSleep, {baseline: {batteryTempC: 29}});
  expect(result.protocol).toMatchObject({maxBatteryTempRiseC: 0.5, readinessTimeoutSec: 10});
  expect(result.configuration).toMatchObject({maxBatteryTempRiseC: 0.5, readinessTimeoutSec: 10});
  expect(result.coldRuns[0]).toMatchObject({status: 'skipped', readiness: {reasons: ['battery-not-cooled']}});
});

describe('charging-policy-v3 in sessions', () => {
  const charging = {...healthyConditions, batteryStatus: 2};

  test('charging during the run violates eligibility but keeps the measurement', async () => {
    jest.mocked(conditions.stopMonitor).mockResolvedValue(stoppedMonitor([charging]));
    const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
    expect(result.status).toBe('complete');
    expect(result.summary.decodeTps?.n).toBe(1);
    expect(result.coldRuns[0].conditions.charging).toBe('violated');
    expect(result.conditionVerdict).toEqual({status: 'pass', reasons: [], charging: 'violated'});
    expect(result.guardsPassed).toBeNull();
  });

  test('unreadable charging state is recorded and never eligible', async () => {
    jest.mocked(conditions.readConditions).mockImplementation(async () =>
      conditions.normalizeConditions({...healthyConditions, batteryStatus: null, currentNowUa: null}));
    const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
    expect(result.coldRuns[0].conditions.before.unavailable).toEqual(expect.arrayContaining(['batteryStatus', 'currentNowUa']));
    expect(result.coldRuns[0].conditions.charging).toBe('unknown');
    expect(result.conditionVerdict.charging).toBe('unknown');
  });

  test('a small current never substitutes for a status: diagnostic only', async () => {
    const quiet = {...healthyConditions, batteryStatus: null, currentNowUa: -8000};
    jest.mocked(conditions.readConditions).mockImplementation(async () => conditions.normalizeConditions(quiet));
    jest.mocked(conditions.stopMonitor).mockResolvedValue(stoppedMonitor([quiet]));
    const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
    expect(result.coldRuns[0].conditions.before.currentNowUa).toBe(-8000);
    expect(result.conditionVerdict.charging).toBe('unknown');
  });

  test('readiness waits while the battery is charging, then measures', async () => {
    jest.mocked(conditions.readConditions)
      .mockResolvedValueOnce(conditions.normalizeConditions(charging))
      .mockResolvedValueOnce(conditions.normalizeConditions(charging));
    const sleep = jest.fn(noSleep);
    const result = await runSession(adapter(), one, config.runs[0], identity, async () => {}, sleep);
    expect(sleep.mock.calls).toEqual([[5000], [5000]]);
    expect(result.coldRuns[0]).toMatchObject({status: 'complete', readiness: {status: 'ready', polls: 3,
      notReady: {charging: 2}}});
    expect(result.conditionVerdict).toEqual({status: 'pass', reasons: [], charging: 'satisfied'});
  });

  test('running on battery is a known state that is not eligible in the tethered lab', async () => {
    const onBattery = {...healthyConditions, batteryStatus: 3, charging: false, plugged: 0};
    jest.mocked(conditions.readConditions).mockImplementation(async () => conditions.normalizeConditions(onBattery));
    jest.mocked(conditions.stopMonitor).mockResolvedValue(stoppedMonitor([onBattery]));
    const result = await runSession(adapter(), one, config.runs[0], identity, async () => {});
    expect(result.status).toBe('complete');
    expect(result.coldRuns[0].conditions.during?.summary.chargingStates).toMatchObject({onBattery: 1, unknown: 0});
    expect(result.coldRuns[0].conditions.charging).toBe('on-battery');
    expect(result.conditionVerdict).toEqual({status: 'pass', reasons: [], charging: 'on-battery'});
  });

  test('charging until the readiness timeout skips the repetition with reason charging', async () => {
    jest.mocked(conditions.readConditions).mockImplementation(async () => conditions.normalizeConditions(charging));
    const engine = adapter();
    const result = await runSession(engine, {...config, readinessTimeoutSec: 10}, config.runs[0], identity,
      async () => {}, noSleep);
    expect(engine.load).not.toHaveBeenCalled();
    expect(result.coldRuns[0]).toMatchObject({status: 'skipped', reason: 'charging',
      readiness: {status: 'timeout', reasons: ['charging']}, conditions: {charging: 'violated'}});
    // Charging is eligibility, not validity: the skipped session is unverified, not a failed condition.
    expect(result).toMatchObject({status: 'failed', stopReason: 'readiness-timeout', guardsPassed: null,
      conditionVerdict: {status: 'unverified', reasons: [], charging: 'violated'}});
  });
});
