import {
  assessCharging, assessConditions, chargingState, assessDuring, assessReadiness, CONDITION_POLICY, monitorPending,
  normalizeConditions, normalizeMonitor, readConditions, startMonitor, stopMonitor,
} from '../src/harness/conditions';
import {awaitReadiness} from '../src/harness/readiness';
import * as conditions from '../src/harness/conditions';

jest.mock('react-native', () => ({NativeModules: {}}));

afterEach(() => jest.restoreAllMocks());

// Tethered at a charge limit: external power connected, battery not charging.
const healthy = {timestampMs: 100, elapsedMs: 100, batteryPct: 80, batteryTempC: 30, charging: true,
  batteryStatus: 4, plugged: 2, currentNowUa: -3000,
  powerSaveMode: false, thermalStatus: 0, thermalHeadroom: 0.3, screenOn: true, foreground: true,
  keyguardLocked: false, focusLossCount: 0, processUptimeMs: 5000, rssMb: 200, pssMb: 100};

test('an empty or explicitly failed native result is unavailable, not complete', () => {
  expect(normalizeConditions({}).probeStatus).toBe('unavailable');
  expect(normalizeConditions({probeStatus: 'unavailable', batteryPct: 80}).probeStatus).toBe('unavailable');
  expect(assessConditions(normalizeConditions({...healthy, probeStatus: 'unavailable'})).status).toBe('unverified');
});

test('normalizes only approved fields and reports missing probes', () => {
  const snapshot = normalizeConditions({...healthy, serial: 'private'});
  expect(snapshot.unavailable).toEqual([]);
  expect(snapshot).not.toHaveProperty('serial');
  expect(normalizeConditions({}).unavailable).toContain('thermalStatus');
});

test.each([{batteryPct: -1}, {thermalStatus: -1}, {thermalStatus: 1.5}, {foreground: 'true'},
  {keyguardLocked: null}, {focusLossCount: 1.5}])('invalid required readings stay unknown: %j', change => {
  const snapshot = normalizeConditions({...healthy, ...change});
  const key = Object.keys(change)[0];
  expect(snapshot).toHaveProperty(key, null);
  expect(snapshot.unavailable).toContain(key);
  expect(assessConditions(snapshot).status).toBe('unverified');
  expect(assessReadiness(snapshot, null)).toMatchObject({ready: false});
});

test.each([{thermalHeadroom: NaN}, {pssMb: Infinity}, {rssMb: -1}, {charging: null}, {batteryStatus: 7},
  {currentNowUa: 1.5}, {timestampMs: NaN}])(
  'optional observations are recorded as unknown without inventing a failure: %j', change => {
    const snapshot = normalizeConditions({...healthy, ...change});
    expect(snapshot).toHaveProperty(Object.keys(change)[0], null);
    expect(assessConditions(snapshot).status).toBe('pass');
  });

test.each([{batteryPct: 49}, {powerSaveMode: true}, {foreground: false},
  {screenOn: false}, {keyguardLocked: true}, {thermalStatus: 3}])('flags observed bad conditions: %j', change => {
  expect(assessConditions(normalizeConditions({...healthy, ...change})).status).toBe('fail');
  expect(assessReadiness(normalizeConditions({...healthy, ...change}), null).ready).toBe(false);
});

test('readiness is stricter than run flags and relative to the first ready temperature', () => {
  const ready = (change: object, baseline: number | null = null) =>
    assessReadiness(normalizeConditions({...healthy, ...change}), baseline);
  expect(ready({})).toEqual({ready: true, reasons: []});
  expect(ready({thermalStatus: 1}).reasons).toEqual(['thermal-not-ready']);
  expect(ready({batteryTempC: 40.5}).reasons).toEqual(['battery-too-hot']);
  expect(ready({batteryTempC: 32.5}, 30).reasons).toEqual(['battery-not-cooled']);
  expect(ready({batteryTempC: 32}, 30).ready).toBe(true);
  // A configuration may override the rise bound (recorded in protocol.maxBatteryTempRiseC).
  expect(assessReadiness(normalizeConditions({...healthy, batteryTempC: 33}), 30, 3).ready).toBe(true);
  expect(assessReadiness(normalizeConditions({...healthy, batteryTempC: 31}), 30, 0.5).reasons).toEqual(['battery-not-cooled']);
  expect(ready({thermalHeadroom: 0.9}).reasons).toEqual(['thermal-headroom-low']);
  // Unsupported headroom is recorded as null and is not a readiness criterion.
  expect(ready({thermalHeadroom: null}).ready).toBe(true);
  expect(assessReadiness(normalizeConditions(null), null).reasons).toContain('probe-incomplete');
});

test('missing or rejecting module returns unknown, never passing evidence', async () => {
  expect(assessConditions(await readConditions()).status).toBe('unverified');
  expect(assessConditions(await readConditions(async () => {throw new Error('private');})).status).toBe('unverified');
  expect(await startMonitor()).toBe('unavailable');
  expect(await stopMonitor()).toMatchObject({status: 'error'});
});

test('unresponsive native probe and monitor calls are bounded and reported as pending', async () => {
  jest.useFakeTimers();
  try {
    const probe = readConditions(() => new Promise(() => {}));
    const start = startMonitor(() => new Promise(() => {}));
    const stop = stopMonitor(() => new Promise(() => {}));
    await jest.advanceTimersByTimeAsync(2000);
    expect(await probe).toMatchObject({probeStatus: 'timeout'});
    expect(await start).toBe('timeout');
    const stopped = await stop;
    expect(stopped.status).toBe('timeout');
    expect(monitorPending(stopped)).toBe(true);
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

// Samples one second apart from an immediate first sample at monitor start.
const monitorRaw = (samples: object[], extra: object = {}) => ({status: 'stopped', intervalMs: 1000,
  startedElapsedMs: 100, stoppedElapsedMs: 100 + samples.length * 1000 - 500,
  ticks: samples.length, dropped: 0, expired: false, maxSampleCostMs: 2, totalSampleCostMs: 5,
  samples: samples.map((sample, index) => ({elapsedMs: 100 + index * 1000, ...sample})), ...extra});
const before = normalizeConditions(healthy);

test('during-run samples pass only with complete coverage', () => {
  const monitor = normalizeMonitor(monitorRaw([healthy, {...healthy, thermalStatus: 2}]));
  expect(monitor.summary).toMatchObject({samples: 2, maxThermalStatus: 2, incompleteSamples: 0,
    externalPower: {connected: 2, disconnected: 0, unknown: 0}, chargingStates: {notCharging: 2, charging: 0, onBattery: 0, unknown: 0}});
  expect(assessDuring(monitor, before, before)).toEqual({status: 'pass', reasons: []});
  // Zero samples, a sampling gap, too few ticks for the duration, expiry or drops are incomplete.
  expect(assessDuring(normalizeMonitor(monitorRaw([])), before, before).status).toBe('unverified');
  expect(assessDuring(normalizeMonitor(monitorRaw([healthy, {...healthy, elapsedMs: 5000}])), before, before).status)
    .toBe('unverified');
  expect(assessDuring(normalizeMonitor(monitorRaw([healthy], {stoppedElapsedMs: 3100})), before, before).status)
    .toBe('unverified');
  expect(assessDuring(normalizeMonitor(monitorRaw([healthy], {stoppedElapsedMs: 900})), before, before).status)
    .toBe('pass');
  for (const extra of [{expired: true}, {dropped: 1}, {ticks: 3}, {startedElapsedMs: null}]) {
    expect(assessDuring(normalizeMonitor(monitorRaw([healthy], extra)), before, before).status).toBe('unverified');
  }
  expect(assessDuring(normalizeMonitor(monitorRaw([{...healthy, thermalStatus: null}])), before, before).status)
    .toBe('unverified');
  expect(assessDuring(normalizeMonitor(null), before, before).status).toBe('unverified');
});

test('the coverage rule is strict but achievable for long fixed-rate monitors', () => {
  // A 300 s run sampled at start + k * 1000 ms (fixed rate, 2 ms late per wake-up) is covered.
  const run = (spacing: number, offset: number, durationMs: number) => {
    const samples = [];
    for (let at = 0; at <= durationMs; at = samples.length * spacing + offset) samples.push({...healthy, elapsedMs: 100 + at});
    return normalizeMonitor({status: 'stopped', intervalMs: 1000, startedElapsedMs: 100, stoppedElapsedMs: 100 + durationMs,
      ticks: samples.length, dropped: 0, expired: false, maxSampleCostMs: 14, totalSampleCostMs: 14 * samples.length,
      samples});
  };
  expect(assessDuring(run(1000, 2, 300_000), before, before).status).toBe('pass');
  // The old fixed-delay loop (1000 ms after a 14 ms sample, 2 ms late) falls behind and is unverified.
  expect(assessDuring(run(1016, 0, 300_000), before, before).status).toBe('unverified');
});

test('sampled failures and focus losses between endpoints are flagged', () => {
  const monitor = normalizeMonitor(monitorRaw([healthy, {...healthy, foreground: false}]));
  expect(assessDuring(monitor, before, before)).toEqual({status: 'fail', reasons: ['not-foreground']});
  const clean = normalizeMonitor(monitorRaw([healthy]));
  const interrupted = normalizeConditions({...healthy, focusLossCount: 1});
  // A brief interruption between 1 Hz samples is still caught by the native focus counter.
  expect(assessDuring(clean, before, interrupted)).toEqual({status: 'fail', reasons: ['interrupted']});
});

test('persisted samples are bounded while the summary covers every sample', () => {
  const samples = Array.from({length: 200}, (_, index) => ({...healthy, batteryTempC: index === 137 ? 39 : 30}));
  const monitor = normalizeMonitor(monitorRaw(samples));
  expect(monitor.samples).toHaveLength(CONDITION_POLICY.run.sampling.persistedSamples);
  expect(monitor.summary).toMatchObject({samples: 200, maxBatteryTempC: 39});
});

describe('charging-policy-v3 (tethered, not charging)', () => {
  const state = (change: object) => chargingState(normalizeConditions({...healthy, ...change}));

  test('full or not-charging statuses are eligible with external power connected', () => {
    expect(state({batteryStatus: 5, charging: true})).toBe('notCharging');
    expect(state({batteryStatus: 4, charging: true, currentNowUa: 900000})).toBe('notCharging');
    // DISCHARGING is a known state of its own, not eligible under charging-policy-v3.
    expect(state({batteryStatus: 3, charging: false})).toBe('onBattery');
  });

  test('an active charging status is charging whatever the current says', () => {
    expect(state({batteryStatus: 2})).toBe('charging');
    expect(state({batteryStatus: 2, currentNowUa: 100})).toBe('charging');
  });

  test('current_now is diagnostic only: without a status the state is unknown', () => {
    // Units and sign are vendor-defined (the Galaxy S23 reports what looks like mA).
    for (const currentNowUa of [-15000, -1500, 0, 20000, 500000, null]) {
      expect(state({batteryStatus: null, currentNowUa})).toBe('unknown');
      expect(state({batteryStatus: 1, currentNowUa})).toBe('unknown');
    }
  });

  test('any charging violates, unknown is never eligible, and measured not-charging satisfies', () => {
    expect(assessCharging({notCharging: 3, charging: 0, onBattery: 0, unknown: 0})).toBe('satisfied');
    expect(assessCharging({notCharging: 3, charging: 1, onBattery: 1, unknown: 0})).toBe('violated');
    expect(assessCharging({notCharging: 3, charging: 0, onBattery: 0, unknown: 1})).toBe('unknown');
    expect(assessCharging({notCharging: 0, charging: 0, onBattery: 0, unknown: 0})).toBe('unknown');
    // On battery is known (not unknown) but not eligible under the tethered lab policy.
    expect(assessCharging({notCharging: 3, charging: 0, onBattery: 1, unknown: 1})).toBe('on-battery');
  });

  test('readiness waits while charging but never for an unknown charging state', () => {
    expect(assessReadiness(normalizeConditions({...healthy, batteryStatus: 2}), null).reasons).toEqual(['charging']);
    expect(assessReadiness(normalizeConditions({...healthy, batteryStatus: null, currentNowUa: null}), null).ready)
      .toBe(true);
  });

  test('the experiment-only record-only mode does not wait for charging, but other rules still apply', () => {
    expect(assessReadiness(normalizeConditions({...healthy, batteryStatus: 2}), null, 2, false))
      .toEqual({ready: true, reasons: []});
    expect(assessReadiness(normalizeConditions({...healthy, batteryStatus: 2, thermalStatus: 1}), null, 2, false).reasons)
      .toEqual(['thermal-not-ready']);
  });

  test('monitor summaries count the charging state of every sample', () => {
    const summary = normalizeMonitor(monitorRaw([healthy, {...healthy, batteryStatus: 2},
      {...healthy, batteryStatus: null, currentNowUa: null}])).summary;
    expect(summary.chargingStates).toEqual({notCharging: 1, charging: 1, onBattery: 0, unknown: 1});
  });
});

describe('bounded readiness wait', () => {
  const sleep = jest.fn(async (_ms: number) => {});
  beforeEach(() => sleep.mockClear());

  test('waits for cooling, then uses the ready sample and records why it waited', async () => {
    jest.spyOn(conditions, 'readConditions')
      .mockResolvedValueOnce(normalizeConditions({...healthy, thermalStatus: 1}))
      .mockResolvedValueOnce(normalizeConditions({...healthy, foreground: false}))
      .mockResolvedValueOnce(normalizeConditions({...healthy, batteryTempC: 31}));
    const baseline = {batteryTempC: null};
    const {result, snapshot} = await awaitReadiness(60, baseline, sleep);
    expect(result).toMatchObject({status: 'ready', polls: 3, reasons: [],
      notReady: {'thermal-not-ready': 1, 'not-foreground': 1}});
    expect(snapshot.batteryTempC).toBe(31);
    expect(baseline.batteryTempC).toBe(31);
    expect(sleep.mock.calls).toEqual([[5000], [5000]]);
  });

  test('times out within its poll budget even if the clock does not advance', async () => {
    jest.spyOn(conditions, 'readConditions').mockResolvedValue(normalizeConditions(null));
    const {result} = await awaitReadiness(20, {batteryTempC: null}, sleep, () => 0);
    expect(result.status).toBe('timeout');
    expect(result.polls).toBe(5);
    expect(result.reasons).toContain('probe-incomplete');
  });

  test('times out on elapsed wall time when probes or sleeps are slow', async () => {
    jest.spyOn(conditions, 'readConditions').mockResolvedValue(normalizeConditions({...healthy, thermalStatus: 1}));
    let clock = 0;
    const {result} = await awaitReadiness(20, {batteryTempC: null}, async () => {clock += 12000;}, () => clock);
    // Overrun is bounded by one slow sleep; the wait never starts another poll past the limit.
    expect(result).toMatchObject({status: 'timeout', polls: 3, waitedMs: 24000, reasons: ['thermal-not-ready']});
  });

  test('a pending probe stops waiting immediately', async () => {
    jest.spyOn(conditions, 'readConditions').mockResolvedValue(normalizeConditions({probeStatus: 'busy'}));
    const {result} = await awaitReadiness(60, {batteryTempC: null}, sleep);
    expect(result.status).toBe('probe-pending');
    expect(sleep).not.toHaveBeenCalled();
  });
});
