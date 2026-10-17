import {NativeModules} from 'react-native';

export interface ConditionSnapshot {
  probeStatus: 'complete' | 'unavailable' | 'timeout' | 'busy';
  timestampMs: number | null;
  batteryPct: number | null;
  batteryTempC: number | null;
  charging: boolean | null;
  powerSaveMode: boolean | null;
  thermalStatus: number | null;
  screenOn: boolean | null;
  foreground: boolean | null;
  keyguardLocked: boolean | null;
  pssMb: number | null;
  unavailable: string[];
}

export interface ConditionAssessment {
  status: 'pass' | 'fail' | 'unverified';
  reasons: string[];
}

export const CONDITION_POLICY = {
  version: 'observed-conditions-v1', mode: 'record-and-flag',
  minBatteryPct: 50, maxThermalStatus: 2, charging: 'record-only',
  memory: 'snapshots-not-peak', coverage: 'endpoints-not-continuous',
} as const;

export function normalizeConditions(raw: unknown): ConditionSnapshot {
  const input = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const number = (key: string, min: number, max: number, integer = false) => {
    const value = input[key];
    return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max &&
      (!integer || Number.isInteger(value)) ? value : null;
  };
  const boolean = (key: string) => typeof input[key] === 'boolean' ? input[key] as boolean : null;
  const values = {
    timestampMs: number('timestampMs', 0, Number.MAX_SAFE_INTEGER),
    batteryPct: number('batteryPct', 0, 100), batteryTempC: number('batteryTempC', -50, 150),
    charging: boolean('charging'), powerSaveMode: boolean('powerSaveMode'),
    thermalStatus: number('thermalStatus', 0, 6, true), screenOn: boolean('screenOn'),
    foreground: boolean('foreground'), keyguardLocked: boolean('keyguardLocked'),
    pssMb: number('pssMb', 0, Number.MAX_SAFE_INTEGER),
  };
  const probeStatus = input.probeStatus === 'timeout' || input.probeStatus === 'busy' ? input.probeStatus :
    raw == null ? 'unavailable' : 'complete';
  return {...values, probeStatus,
    unavailable: Object.entries(values).filter(([, value]) => value === null).map(([key]) => key)};
}

export const probePending = (snapshot: ConditionSnapshot) =>
  snapshot.probeStatus === 'timeout' || snapshot.probeStatus === 'busy';

/** Flags only these endpoint checks; a pass is NOT full protocol validation. */
export function assessConditions(snapshot: ConditionSnapshot): ConditionAssessment {
  const reasons: string[] = [];
  if (snapshot.batteryPct !== null && snapshot.batteryPct < CONDITION_POLICY.minBatteryPct) reasons.push('low-battery');
  if (snapshot.thermalStatus !== null && snapshot.thermalStatus > CONDITION_POLICY.maxThermalStatus) reasons.push('severe-thermal-state');
  if (snapshot.powerSaveMode === true) reasons.push('power-saving');
  if (snapshot.screenOn === false) reasons.push('screen-off');
  if (snapshot.foreground === false) reasons.push('not-foreground');
  if (snapshot.keyguardLocked === true) reasons.push('device-locked');
  return {status: reasons.length ? 'fail' : snapshot.unavailable.length || snapshot.probeStatus !== 'complete' ? 'unverified' : 'pass', reasons};
}

/** A missing/broken bridge must neither invent readings nor block cleanup. */
export async function readConditions(
  sample: () => Promise<unknown> = () => NativeModules.BenchConditions.sample(),
): Promise<ConditionSnapshot> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const raw = await Promise.race([
      Promise.resolve().then(sample),
      new Promise<unknown>(resolve => {timer = setTimeout(() => resolve({probeStatus: 'timeout'}), 2000);}),
    ]);
    return normalizeConditions(raw);
  } catch {
    return normalizeConditions(null);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
