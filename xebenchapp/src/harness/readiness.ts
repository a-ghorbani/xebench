import {assessReadiness, CONDITION_POLICY, probePending, readConditions} from './conditions';
import type {ConditionSnapshot} from './conditions';

/** Shared across a configuration so later runs must return near the first ready temperature. */
export interface ReadinessBaseline {
  batteryTempC: number | null;
}

export interface ReadinessResult {
  /** `probe-pending`: a native probe timed out or was busy; the configuration must abort. */
  status: 'ready' | 'timeout' | 'probe-pending';
  waitedMs: number;
  polls: number;
  timeoutSec: number;
  baselineBatteryTempC: number | null;
  /** Whether a measured thermal headroom took part in the ready decision. */
  headroomApplied: boolean;
  /** Count of polls that reported each not-ready reason. */
  notReady: Record<string, number>;
  /** Reasons from the final poll; empty only when ready. */
  reasons: string[];
}

/**
 * Polls conditions until ready or the bounded wait expires. Both elapsed wall time and
 * a poll budget bound the loop, so a frozen clock or slow probes cannot extend it.
 * The final snapshot becomes the repetition's `before` endpoint.
 */
export async function awaitReadiness(
  timeoutSec: number, baseline: ReadinessBaseline,
  sleep: (ms: number) => Promise<void>, now: () => number = Date.now,
  maxRiseC: number = CONDITION_POLICY.readiness.maxBatteryTempRiseC, waitWhileCharging = true,
): Promise<{result: ReadinessResult; snapshot: ConditionSnapshot}> {
  const pollMs = CONDITION_POLICY.readiness.pollSec * 1000;
  const maxPolls = Math.floor(timeoutSec * 1000 / pollMs) + 1;
  const started = now();
  const notReady: Record<string, number> = {};
  const baselineBatteryTempC = baseline.batteryTempC;
  let confirmedHeadroom = false;
  for (let polls = 1; ; polls++) {
    const snapshot = await readConditions();
    const waitedMs = Math.max(0, now() - started);
    const finish = (status: ReadinessResult['status'], reasons: string[]) => ({snapshot, result: {
      status, waitedMs, polls, timeoutSec, baselineBatteryTempC,
      headroomApplied: snapshot.thermalHeadroom !== null, notReady, reasons,
    }});
    if (probePending(snapshot)) return finish('probe-pending', ['probe-pending']);
    const {ready, reasons} = assessReadiness(snapshot, baseline.batteryTempC, maxRiseC, waitWhileCharging);
    if (ready && snapshot.thermalHeadroom === null && !confirmedHeadroom) {
      // Bounded: at most one extra poll per readiness wait.
      confirmedHeadroom = true;
      await sleep(CONDITION_POLICY.readiness.headroomConfirmMs);
      continue;
    }
    if (ready) {
      if (baseline.batteryTempC === null) baseline.batteryTempC = snapshot.batteryTempC;
      return finish('ready', []);
    }
    for (const reason of reasons) notReady[reason] = (notReady[reason] ?? 0) + 1;
    if (polls >= maxPolls || waitedMs + pollMs > timeoutSec * 1000) return finish('timeout', reasons);
    await sleep(pollMs);
  }
}
