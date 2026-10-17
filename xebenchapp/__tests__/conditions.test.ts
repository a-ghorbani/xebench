import {assessConditions, normalizeConditions, readConditions} from '../src/harness/conditions';

jest.mock('react-native', () => ({NativeModules: {}}));

const healthy = {timestampMs: 100, batteryPct: 80, batteryTempC: 30, charging: true,
  powerSaveMode: false, thermalStatus: 0, screenOn: true, foreground: true,
  keyguardLocked: false, pssMb: 100};

test('normalizes only approved fields and reports missing probes', () => {
  const snapshot = normalizeConditions({...healthy, serial: 'private'});
  expect(snapshot.unavailable).toEqual([]);
  expect(snapshot).not.toHaveProperty('serial');
  expect(normalizeConditions({}).unavailable).toContain('thermalStatus');
});

test.each([{batteryPct: -1}, {thermalStatus: -1}, {thermalStatus: 1.5},
  {pssMb: Infinity}, {foreground: 'true'}, {timestampMs: NaN}])('invalid readings stay unknown: %j', change => {
  const snapshot = normalizeConditions({...healthy, ...change});
  const key = Object.keys(change)[0];
  expect(snapshot).toHaveProperty(key, null);
  expect(snapshot.unavailable).toContain(key);
  expect(assessConditions(snapshot).status).toBe('unverified');
});

test('observed checks pass only with complete evidence; charging is record-only', () => {
  expect(assessConditions(normalizeConditions(healthy))).toMatchObject({status: 'pass', reasons: []});
  expect(assessConditions(normalizeConditions({...healthy, charging: null})).status).toBe('unverified');
});

test.each([{batteryPct: 49}, {powerSaveMode: true}, {foreground: false},
  {screenOn: false}, {keyguardLocked: true}, {thermalStatus: 3}])('flags observed bad conditions: %j', change => {
  expect(assessConditions(normalizeConditions({...healthy, ...change})).status).toBe('fail');
});

test('missing or rejecting module returns unknown, never passing evidence', async () => {
  expect(assessConditions(await readConditions()).status).toBe('unverified');
  expect(assessConditions(await readConditions(async () => {throw new Error('private');})).status).toBe('unverified');
});

test('unresponsive native probe is bounded', async () => {
  jest.useFakeTimers();
  try {
    const result = readConditions(() => new Promise(() => {}));
    await jest.advanceTimersByTimeAsync(2000);
    expect(assessConditions(await result).status).toBe('unverified');
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});
