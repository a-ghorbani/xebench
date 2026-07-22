/**
 * xebench v0.1 standardized protocol.
 *
 * Per engine session:
 *   0. Guard checks (battery >= 50%, not power-save; charging recorded).
 *   1. n=3 cold runs: [mem baseline] -> load -> std prompt (~512 tok) ->
 *      128-token greedy decode -> release -> 30 s cooldown + GC hint.
 *      "Cold" = fresh engine instance in a warm process (see README caveat).
 *   2. Optional sustained phase: load once, loop short-prompt/128-token decode
 *      back-to-back for 5 min; per-iteration decode t/s + thermal.
 *   3. One raw JSONL line appended to results/raw-<runId>.jsonl on device.
 */

import { HARNESS_VERSION } from './types';
import type {
  ColdRunRecord,
  DeviceInfo,
  EngineSessionRecord,
  SustainedIteration,
  SustainedResult,
} from './types';
import type { EngineAdapter } from './adapters/EngineAdapter';
import { MemorySampler, probe, sleep } from './probe';
import { aggregate, median, round1 } from './stats';
import {
  MAX_DECODE_TOKENS,
  STANDARD_PROMPT,
  STANDARD_PROMPT_LABEL,
  SUSTAINED_PROMPT,
} from './prompts';

export interface ProtocolOptions {
  nColdRuns: number; // default 3
  cooldownSec: number; // default 30
  sustainedTargetSec: number; // default 300 (5 min); 0 = skip
  runId: string;
  /** true if another engine already ran in this app process */
  processReused: boolean;
  log: (msg: string) => void;
}

export const DEFAULT_PROTOCOL: Omit<ProtocolOptions, 'runId' | 'log' | 'processReused'> = {
  nColdRuns: 3,
  cooldownSec: 30,
  sustainedTargetSec: 300,
};

const MIN_BATTERY_PCT = 50;

async function guardChecks(log: (m: string) => void): Promise<{ ok: boolean; notes: string[] }> {
  const notes: string[] = [];
  const t = await probe.getThermalSnapshot();
  let ok = true;
  if (t.batteryPct >= 0 && t.batteryPct < MIN_BATTERY_PCT) {
    ok = false;
    notes.push(`battery ${t.batteryPct}% < ${MIN_BATTERY_PCT}% minimum`);
  }
  if (t.powerSaveMode) {
    ok = false;
    notes.push('power-save mode is ON — CPU is capped, numbers would be invalid');
  }
  if (t.charging) {
    notes.push('device is CHARGING — allowed but recorded (affects thermals)');
  }
  if (t.thermalStatus >= 2) {
    ok = false;
    notes.push(`thermal status ${t.thermalStatus} (>= MODERATE) before start — let the device cool`);
  }
  notes.forEach((n) => log(`guard: ${n}`));
  return { ok, notes };
}

async function coldRun(
  adapter: EngineAdapter,
  runIndex: number,
  log: (m: string) => void,
): Promise<ColdRunRecord> {
  const thermalBefore = await probe.getThermalSnapshot();
  const baseline = await probe.getMemorySnapshot();

  const sampler = new MemorySampler();
  await sampler.start(500);

  const loadMs = await adapter.load();
  log(`  run ${runIndex}: loaded in ${loadMs} ms`);

  const metrics = await adapter.benchOnce({
    prompt: STANDARD_PROMPT,
    maxDecodeTokens: MAX_DECODE_TOKENS,
  });

  const { peakPssMb, vmHwmMb } = await sampler.stop();
  await adapter.release();
  const thermalAfter = await probe.getThermalSnapshot();

  const rec: ColdRunRecord = {
    ...metrics,
    loadMs,
    runIndex,
    baselinePssMb: round1(baseline.totalPssMb),
    peakPssMb: round1(peakPssMb),
    deltaPssMb: round1(peakPssMb - baseline.totalPssMb),
    vmHwmMb: round1(vmHwmMb),
    thermalBefore,
    thermalAfter,
  };
  log(
    `  run ${runIndex}: ttft=${rec.ttftMs}ms pp=${round1(rec.prefillTps)}t/s ` +
      `(${rec.promptTokens} tok, ${rec.prefillMethod}) tg=${round1(rec.decodeTps)}t/s ` +
      `(${rec.decodeTokens} tok) peakPSS=${rec.peakPssMb}MB (Δ${rec.deltaPssMb}MB)`,
  );
  return rec;
}

async function sustainedPhase(
  adapter: EngineAdapter,
  targetSec: number,
  log: (m: string) => void,
): Promise<SustainedResult> {
  const thermalBefore = await probe.getThermalSnapshot();
  await adapter.load();
  const iterations: SustainedIteration[] = [];
  const tStart = Date.now();

  while ((Date.now() - tStart) / 1000 < targetSec) {
    const m = await adapter.benchOnce({
      prompt: SUSTAINED_PROMPT,
      maxDecodeTokens: MAX_DECODE_TOKENS,
    });
    const th = await probe.getThermalSnapshot();
    const it: SustainedIteration = {
      tSec: round1((Date.now() - tStart) / 1000),
      decodeTps: round1(m.decodeTps),
      ttftMs: m.ttftMs,
      thermalStatus: th.thermalStatus,
      thermalHeadroom: th.thermalHeadroom,
      batteryTempC: th.batteryTempC,
    };
    iterations.push(it);
    log(`  sustained t=${it.tSec}s tg=${it.decodeTps}t/s thermal=${it.thermalStatus}`);
  }

  await adapter.release();
  const thermalAfter = await probe.getThermalSnapshot();
  const durationSec = round1((Date.now() - tStart) / 1000);

  const firstMin = iterations.filter((i) => i.tSec <= 60).map((i) => i.decodeTps);
  const lastMin = iterations
    .filter((i) => i.tSec >= durationSec - 60)
    .map((i) => i.decodeTps);
  const firstMinuteTgTps = round1(median(firstMin));
  const lastMinuteTgTps = round1(median(lastMin));

  return {
    durationSec,
    iterations,
    firstMinuteTgTps,
    lastMinuteTgTps,
    degradationPct: round1(((firstMinuteTgTps - lastMinuteTgTps) / firstMinuteTgTps) * 100),
    thermalBefore,
    thermalAfter,
  };
}

export async function runEngineSession(
  adapter: EngineAdapter,
  opts: ProtocolOptions,
): Promise<EngineSessionRecord> {
  const { log } = opts;
  log(`=== ${adapter.engine} (${adapter.quantLabel}) — ${HARNESS_VERSION} ===`);

  await probe.setKeepScreenOn(true).catch(() => {});
  const guards = await guardChecks(log);
  const deviceInfo: DeviceInfo = await probe.getDeviceInfo();
  if (!guards.ok) {
    log('GUARDS FAILED — aborting session (fix the conditions above and re-run).');
  }

  const coldRuns: ColdRunRecord[] = [];
  let sustained: SustainedResult | null = null;

  if (guards.ok) {
    for (let i = 1; i <= opts.nColdRuns; i++) {
      coldRuns.push(await coldRun(adapter, i, log));
      if (i < opts.nColdRuns || opts.sustainedTargetSec > 0) {
        log(`  cooldown ${opts.cooldownSec}s + GC...`);
        await probe.requestGc().catch(() => {});
        await sleep(opts.cooldownSec * 1000);
      }
    }

    // Cross-engine sanity check: same raw prompt must tokenize to ~same count
    const counts = coldRuns.map((r) => r.promptTokens);
    if (Math.max(...counts) - Math.min(...counts) > 0) {
      log(`  WARN: prompt token count varied across runs: ${counts.join(',')}`);
    }

    if (opts.sustainedTargetSec > 0) {
      log(`  sustained phase: ${opts.sustainedTargetSec}s decode-heavy loop...`);
      sustained = await sustainedPhase(adapter, opts.sustainedTargetSec, log);
      log(
        `  sustained: first-min=${sustained.firstMinuteTgTps}t/s ` +
          `last-min=${sustained.lastMinuteTgTps}t/s degradation=${sustained.degradationPct}%`,
      );
    }
  }

  await probe.setKeepScreenOn(false).catch(() => {});

  const modelPath = adapter.modelFile;
  const record: EngineSessionRecord = {
    schema: 'xebench-raw',
    harnessVersion: HARNESS_VERSION,
    runId: opts.runId,
    timestampIso: new Date().toISOString(),
    engine: adapter.engine,
    engineVersion: adapter.engineVersion(),
    backend: 'cpu',
    platform: 'android',
    deviceInfo,
    model: adapter.modelLabel,
    quant: adapter.quantLabel,
    modelFile: modelPath,
    modelFileSizeMb: null, // App fills this in when it knows the absolute path
    protocol: {
      nColdRuns: opts.nColdRuns,
      promptLabel: STANDARD_PROMPT_LABEL,
      promptChars: STANDARD_PROMPT.length,
      maxDecodeTokens: MAX_DECODE_TOKENS,
      cooldownSec: opts.cooldownSec,
      sustainedTargetSec: opts.sustainedTargetSec,
      ...adapter.settings(),
      coldDefinition:
        'fresh engine instance per run in a warm app process; NOT an app restart',
    },
    coldRuns,
    summary:
      coldRuns.length > 0
        ? {
            ttftMs: aggregate(coldRuns.map((r) => r.ttftMs)),
            prefillTps: aggregate(coldRuns.map((r) => r.prefillTps)),
            decodeTps: aggregate(coldRuns.map((r) => r.decodeTps)),
            peakPssMb: aggregate(coldRuns.map((r) => r.peakPssMb)),
            deltaPssMb: aggregate(coldRuns.map((r) => r.deltaPssMb)),
            loadMs: aggregate(coldRuns.map((r) => r.loadMs)),
          }
        : (null as unknown as EngineSessionRecord['summary']),
    sustained,
    processReused: opts.processReused,
    guardsPassed: guards.ok,
    guardNotes: guards.notes,
  };

  const path = await probe.appendResultLine(
    `raw-${opts.runId}.jsonl`,
    JSON.stringify(record),
  );
  log(`session written -> ${path}`);
  return record;
}
