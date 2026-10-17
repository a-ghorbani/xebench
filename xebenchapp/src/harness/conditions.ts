import {NativeModules} from 'react-native';

export interface ConditionSnapshot {
  probeStatus: 'complete' | 'unavailable' | 'timeout' | 'busy';
  timestampMs: number | null;
  /** SystemClock.elapsedRealtime at sampling; used for sample-gap checks. */
  elapsedMs: number | null;
  batteryPct: number | null;
  batteryTempC: number | null;
  /** External power connected (any plug type, including a full battery on USB). */
  charging: boolean | null;
  /** Raw BatteryManager EXTRA_STATUS: 1 unknown, 2 charging, 3 discharging, 4 not charging, 5 full. */
  batteryStatus: number | null;
  /** Raw BatteryManager EXTRA_PLUGGED bitmask (0 none, 1 AC, 2 USB, 4 wireless, 8 dock). */
  plugged: number | null;
  /** Raw BATTERY_PROPERTY_CURRENT_NOW, diagnostic only: documented as µA, but sign and unit vary by vendor. */
  currentNowUa: number | null;
  powerSaveMode: boolean | null;
  thermalStatus: number | null;
  /** PowerManager.getThermalHeadroom(0); null when unsupported or rate-limited. */
  thermalHeadroom: number | null;
  screenOn: boolean | null;
  foreground: boolean | null;
  keyguardLocked: boolean | null;
  /** Process-lifetime count of window-focus losses (interruptions between samples). */
  focusLossCount: number | null;
  processUptimeMs: number | null;
  /** Sampled VmRSS in MiB; includes shared file-backed pages and is not peak memory. */
  rssMb: number | null;
  /** Endpoint PSS in MiB only (not sampled during the run); not peak model memory. */
  pssMb: number | null;
  unavailable: string[];
}

export interface ConditionAssessment {
  status: 'pass' | 'fail' | 'unverified';
  reasons: string[];
}

/** Signals whose absence prevents a pass. Headroom and memory are optional observations. */
export const REQUIRED_SIGNALS = ['batteryPct', 'batteryTempC', 'powerSaveMode', 'thermalStatus',
  'screenOn', 'foreground', 'keyguardLocked', 'focusLossCount'] as const;

export const SAMPLING = {intervalMs: 1000, maxDurationMs: 15 * 60 * 1000, persistedSamples: 30,
  maxGapFactor: 2} as const;

export const CONDITION_POLICY = {
  version: 'run-conditions-v2',
  required: REQUIRED_SIGNALS,
  readiness: {
    mode: 'wait-then-skip', pollSec: 5, maxThermalStatus: 0, maxBatteryTempC: 40,
    maxBatteryTempRiseC: 2, maxThermalHeadroom: 0.8, headroom: 'applied-when-measured',
    // A first headroom call can be NaN; one confirmation poll retries before accepting null.
    headroomConfirmMs: 1500,
  },
  run: {
    mode: 'record-and-flag', minBatteryPct: 50, maxThermalStatus: 2,
    sampling: {...SAMPLING, signals: 'cheap-native-reads-no-pss'}, interruptions: 'focus-loss-count',
  },
  memory: 'endpoint-pss-and-sampled-rss-not-peak',
} as const;

/**
 * Owner decision (docs/RUN-CONDITIONS.md): lab phones stay USB-tethered, so external power is
 * allowed, but active charging is not. Eligibility only; it never removes a measurement.
 */
export const CHARGING_POLICY = {
  version: 'charging-policy-v3', name: 'tethered-not-charging', externalPower: 'allowed',
  // Eligible BatteryManager statuses: not charging (e.g. at a charge limit) and full.
  notChargingStatuses: [4, 5], chargingStatus: 2,
  // DISCHARGING: known, but not eligible in the tethered lab (a later policy may admit it, #7).
  dischargingStatus: 3, onBattery: 'not-eligible',
  // current_now units and sign are vendor-defined (the S23 looks like mA): recorded, never decisive.
  currentNow: 'diagnostic-only',
  readiness: 'wait-while-charging', unknown: 'never-eligible', effect: 'publication-eligibility-only',
} as const;

const NUMBERS: Record<string, [number, number, boolean?]> = {
  timestampMs: [0, Number.MAX_SAFE_INTEGER], elapsedMs: [0, Number.MAX_SAFE_INTEGER], batteryPct: [0, 100], batteryTempC: [-50, 150],
  batteryStatus: [1, 5, true], plugged: [0, 255, true], currentNowUa: [-1e9, 1e9, true],
  thermalStatus: [0, 6, true], thermalHeadroom: [0, 100], focusLossCount: [0, Number.MAX_SAFE_INTEGER, true],
  processUptimeMs: [0, Number.MAX_SAFE_INTEGER], rssMb: [0, Number.MAX_SAFE_INTEGER],
  pssMb: [0, Number.MAX_SAFE_INTEGER],
};
const BOOLEANS = ['charging', 'powerSaveMode', 'screenOn', 'foreground', 'keyguardLocked'];

export function normalizeConditions(raw: unknown): ConditionSnapshot {
  const input = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const values: Record<string, number | boolean | null> = {};
  for (const [key, [min, max, integer]] of Object.entries(NUMBERS)) {
    const value = input[key];
    values[key] = typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max &&
      (!integer || Number.isInteger(value)) ? value : null;
  }
  for (const key of BOOLEANS) {
    values[key] = typeof input[key] === 'boolean' ? input[key] as boolean : null;
  }
  // An empty or explicitly failed native result is unavailable, never a complete probe.
  const probeStatus = input.probeStatus === 'timeout' || input.probeStatus === 'busy' ? input.probeStatus :
    raw == null || input.probeStatus === 'unavailable' || Object.values(values).every(value => value === null) ?
      'unavailable' : 'complete';
  return {...values as unknown as Omit<ConditionSnapshot, 'probeStatus' | 'unavailable'>, probeStatus,
    unavailable: Object.entries(values).filter(([, value]) => value === null).map(([key]) => key)};
}

export const probePending = (snapshot: ConditionSnapshot) =>
  snapshot.probeStatus === 'timeout' || snapshot.probeStatus === 'busy';

const missingRequired = (snapshot: ConditionSnapshot) =>
  REQUIRED_SIGNALS.filter(key => snapshot[key] === null);

/** Observed run-validity failures for one snapshot (endpoint or sample). */
export function conditionFailures(snapshot: ConditionSnapshot): string[] {
  const reasons: string[] = [];
  const {run} = CONDITION_POLICY;
  if (snapshot.batteryPct !== null && snapshot.batteryPct < run.minBatteryPct) reasons.push('low-battery');
  if (snapshot.thermalStatus !== null && snapshot.thermalStatus > run.maxThermalStatus) reasons.push('severe-thermal-state');
  if (snapshot.powerSaveMode === true) reasons.push('power-saving');
  if (snapshot.screenOn === false) reasons.push('screen-off');
  if (snapshot.foreground === false) reasons.push('not-foreground');
  if (snapshot.keyguardLocked === true) reasons.push('device-locked');
  return reasons;
}

/** A pass needs a complete probe with every required signal measured and no failure. */
export function assessConditions(snapshot: ConditionSnapshot): ConditionAssessment {
  const reasons = conditionFailures(snapshot);
  return {status: reasons.length ? 'fail' :
    missingRequired(snapshot).length || snapshot.probeStatus !== 'complete' ? 'unverified' : 'pass', reasons};
}

/** Readiness before loading: stricter thermal limits; unknown required evidence is never ready. */
export function assessReadiness(snapshot: ConditionSnapshot, baselineTempC: number | null,
  maxRiseC: number = CONDITION_POLICY.readiness.maxBatteryTempRiseC) {
  const {readiness} = CONDITION_POLICY;
  const reasons = snapshot.probeStatus === 'complete' ? [] : ['probe-incomplete'];
  reasons.push(...missingRequired(snapshot).map(key => `unknown-${key}`), ...conditionFailures(snapshot));
  if (snapshot.thermalStatus !== null && snapshot.thermalStatus > readiness.maxThermalStatus) reasons.push('thermal-not-ready');
  if (snapshot.batteryTempC !== null && snapshot.batteryTempC > readiness.maxBatteryTempC) reasons.push('battery-too-hot');
  if (snapshot.batteryTempC !== null && baselineTempC !== null &&
      snapshot.batteryTempC > baselineTempC + maxRiseC) reasons.push('battery-not-cooled');
  if (snapshot.thermalHeadroom !== null && snapshot.thermalHeadroom > readiness.maxThermalHeadroom) reasons.push('thermal-headroom-low');
  // Eligibility, not validity: wait for active charging to stop, but never for an unknown state.
  if (chargingState(snapshot) === 'charging') reasons.push('charging');
  return {ready: reasons.length === 0, reasons: [...new Set(reasons)]};
}

export interface MonitorSummary {
  samples: number;
  incompleteSamples: number;
  failures: string[];
  maxThermalStatus: number | null;
  maxThermalHeadroom: number | null;
  maxBatteryTempC: number | null;
  minBatteryPct: number | null;
  maxRssMb: number | null;
  /** Largest gap between monitor start, consecutive samples and stop; null if any time is unknown. */
  maxGapMs: number | null;
  externalPower: {connected: number; disconnected: number; unknown: number};
  chargingStates: ChargingCounts;
}

export interface MonitorResult {
  status: 'complete' | 'unavailable' | 'busy' | 'timeout' | 'stuck' | 'error' | 'not-running';
  intervalMs: number | null;
  startedElapsedMs: number | null;
  stoppedElapsedMs: number | null;
  ticks: number | null;
  dropped: number | null;
  expired: boolean | null;
  maxSampleCostMs: number | null;
  totalSampleCostMs: number | null;
  summary: MonitorSummary;
  /** Evenly spaced subset (bounded record size); the summary covers every sample. */
  samples: ConditionSnapshot[];
}

const extreme = (values: (number | null)[], pick: (...items: number[]) => number) => {
  const known = values.filter((value): value is number => value !== null);
  return known.length ? pick(...known) : null;
};

function maxGap(times: (number | null)[]): number | null {
  if (times.some(time => time === null)) return null;
  let gap = 0;
  for (let index = 1; index < times.length; index++) {
    const step = (times[index] as number) - (times[index - 1] as number);
    if (step < 0) return null;
    gap = Math.max(gap, step);
  }
  return gap;
}

export function summarizeSamples(samples: ConditionSnapshot[], startedMs: number | null = null,
  stoppedMs: number | null = null): MonitorSummary {
  const count = (value: boolean | null) => samples.filter(sample => sample.charging === value).length;
  return {
    samples: samples.length,
    incompleteSamples: samples.filter(sample => sample.probeStatus !== 'complete' || missingRequired(sample).length).length,
    failures: [...new Set(samples.flatMap(conditionFailures))],
    maxThermalStatus: extreme(samples.map(sample => sample.thermalStatus), Math.max),
    maxThermalHeadroom: extreme(samples.map(sample => sample.thermalHeadroom), Math.max),
    maxBatteryTempC: extreme(samples.map(sample => sample.batteryTempC), Math.max),
    minBatteryPct: extreme(samples.map(sample => sample.batteryPct), Math.min),
    maxRssMb: extreme(samples.map(sample => sample.rssMb), Math.max),
    maxGapMs: maxGap([startedMs, ...samples.map(sample => sample.elapsedMs), stoppedMs]),
    externalPower: {connected: count(true), disconnected: count(false), unknown: count(null)},
    chargingStates: samples.reduce((counts, sample) => {
      counts[chargingState(sample)]++;
      return counts;
    }, emptyChargingCounts()),
  };
}

function decimate<T>(items: T[], limit: number): T[] {
  if (items.length <= limit) return items;
  return Array.from({length: limit}, (_, index) => items[Math.round(index * (items.length - 1) / (limit - 1))]);
}

const count = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
const duration = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

export function normalizeMonitor(raw: unknown, fallback: MonitorResult['status'] = 'unavailable'): MonitorResult {
  const input = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const stopped = input.status === 'stopped';
  const samples = stopped && Array.isArray(input.samples) ?
    input.samples.slice(0, 10000).map(normalizeConditions) : [];
  const status = stopped ? 'complete' :
    input.status === 'stuck' || input.status === 'not-running' ? input.status : fallback;
  const startedElapsedMs = duration(input.startedElapsedMs);
  const stoppedElapsedMs = duration(input.stoppedElapsedMs);
  return {
    status, intervalMs: duration(input.intervalMs), startedElapsedMs, stoppedElapsedMs,
    ticks: count(input.ticks), dropped: count(input.dropped),
    expired: typeof input.expired === 'boolean' ? input.expired : null,
    maxSampleCostMs: duration(input.maxSampleCostMs), totalSampleCostMs: duration(input.totalSampleCostMs),
    summary: summarizeSamples(samples, startedElapsedMs, stoppedElapsedMs),
    samples: decimate(samples, SAMPLING.persistedSamples),
  };
}

/**
 * Coverage needs at least one sample (taken at start), no gap longer than
 * GAP_FACTOR intervals between start, samples and stop, and the expected tick count.
 */
function sampledThroughout(monitor: MonitorResult): boolean {
  const {intervalMs, startedElapsedMs, stoppedElapsedMs, ticks, summary} = monitor;
  // The interval and duration must be the policy's, or a sparse monitor could claim coverage.
  if (intervalMs !== SAMPLING.intervalMs || startedElapsedMs === null || stoppedElapsedMs === null || ticks === null ||
      summary.maxGapMs === null || summary.samples < 1 || stoppedElapsedMs < startedElapsedMs ||
      stoppedElapsedMs - startedElapsedMs > SAMPLING.maxDurationMs) return false;
  return summary.maxGapMs <= SAMPLING.maxGapFactor * intervalMs &&
    ticks >= Math.floor((stoppedElapsedMs - startedElapsedMs) / intervalMs);
}

/** True when a monitor may still be running natively and could overlap later work. */
export const monitorPending = (monitor: MonitorResult) =>
  ['busy', 'timeout', 'stuck', 'error'].includes(monitor.status);

/**
 * The whole run was sampled at the policy rate: a stopped, unexpired monitor that kept
 * every tick and has no gap. Charging eligibility needs this too, or the unsampled part
 * of a run (for example after expiry) could hide charging.
 */
export const monitorCovered = (monitor: MonitorResult) =>
  monitor.status === 'complete' && monitor.expired === false && monitor.dropped === 0 &&
  monitor.ticks === monitor.summary.samples && sampledThroughout(monitor);

/**
 * During-run evidence: sampled failures plus focus losses between the endpoints. Incomplete
 * coverage (missing monitor, expiry, dropped or unknown samples) stays unverified.
 */
export function assessDuring(monitor: MonitorResult, before: ConditionSnapshot, after: ConditionSnapshot): ConditionAssessment {
  const reasons = [...monitor.summary.failures];
  const interrupted = before.focusLossCount !== null && after.focusLossCount !== null &&
    after.focusLossCount > before.focusLossCount;
  if (interrupted) reasons.push('interrupted');
  const complete = monitorCovered(monitor) && monitor.summary.incompleteSamples === 0 &&
    before.focusLossCount !== null && after.focusLossCount !== null;
  return {status: reasons.length ? 'fail' : complete ? 'pass' : 'unverified', reasons: [...new Set(reasons)]};
}

export type ChargingStatus = 'satisfied' | 'violated' | 'on-battery' | 'unknown';
export type ChargingState = 'notCharging' | 'charging' | 'onBattery' | 'unknown';
export type ChargingCounts = Record<ChargingState, number>;

/** One observation under charging-policy-v3: decided by the battery status alone. */
export function chargingState(snapshot: ConditionSnapshot): ChargingState {
  const status = snapshot.batteryStatus;
  if (status !== null && (CHARGING_POLICY.notChargingStatuses as readonly number[]).includes(status)) return 'notCharging';
  if (status === CHARGING_POLICY.chargingStatus) return 'charging';
  return status === CHARGING_POLICY.dischargingStatus ? 'onBattery' : 'unknown';
}

export const emptyChargingCounts = (): ChargingCounts => ({notCharging: 0, charging: 0, onBattery: 0, unknown: 0});

/**
 * Any active charging violates the policy. Running on battery is a known state, but this lab
 * policy (tethered) does not admit it. Every observation must be measured to satisfy it.
 */
export function assessCharging(counts: ChargingCounts): ChargingStatus {
  if (counts.charging) return 'violated';
  if (counts.onBattery) return 'on-battery';
  return counts.unknown || !counts.notCharging ? 'unknown' : 'satisfied';
}

type Native = {sample(): Promise<unknown>; startMonitor(intervalMs: number, maxDurationMs: number): Promise<unknown>;
  stopMonitor(): Promise<unknown>};
const native = () => NativeModules.BenchConditions as Native;

/** Bounded native call: a late response is ignored and reported as a timeout. */
async function bounded(call: () => Promise<unknown>, timeoutMs = 2000): Promise<{value: unknown} | 'timeout' | 'error'> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      Promise.resolve().then(call).then(value => ({value})),
      new Promise<'timeout'>(resolve => {timer = setTimeout(() => resolve('timeout'), timeoutMs);}),
    ]);
  } catch {
    return 'error';
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/** A missing/broken bridge must neither invent readings nor block cleanup. */
export async function readConditions(sample: () => Promise<unknown> = () => native().sample()): Promise<ConditionSnapshot> {
  const result = await bounded(sample);
  return normalizeConditions(result === 'timeout' ? {probeStatus: 'timeout'} : result === 'error' ? null : result.value);
}

export async function startMonitor(
  start: () => Promise<unknown> = () => native().startMonitor(SAMPLING.intervalMs, SAMPLING.maxDurationMs),
): Promise<'started' | 'busy' | 'timeout' | 'unavailable'> {
  const result = await bounded(start);
  if (result === 'timeout') return 'timeout';
  const status = result !== 'error' && result.value && typeof result.value === 'object' ?
    (result.value as Record<string, unknown>).status : null;
  return status === 'started' || status === 'busy' ? status : 'unavailable';
}

export async function stopMonitor(stop: () => Promise<unknown> = () => native().stopMonitor()): Promise<MonitorResult> {
  // A rejected or late stop may leave native sampling running: both are pending, not unavailable.
  const result = await bounded(stop);
  return typeof result === 'string' ? normalizeMonitor(null, result) : normalizeMonitor(result.value, 'error');
}
