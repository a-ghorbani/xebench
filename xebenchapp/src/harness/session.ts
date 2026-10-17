import type {EngineAdapter, ExecutionReport, LlamaRnEffectiveSettings} from './adapters/EngineAdapter';
import type {BuildIdentity} from './build';
import type {Aggregate, SingleRunMetrics} from './types';
import type {ReferenceConfig, ReferenceRun} from './config';
import {STANDARD_PROMPT, STANDARD_PROMPT_LABEL} from './prompts';
import {
  assessCharging, assessConditions, assessDuring, CHARGING_POLICY, chargingState, CONDITION_POLICY,
  emptyChargingCounts, monitorCovered, monitorPending,
  normalizeMonitor, probePending, readConditions, startMonitor, stopMonitor,
} from './conditions';
import type {ChargingCounts, ChargingStatus, ConditionAssessment, ConditionSnapshot, MonitorResult} from './conditions';
import {awaitReadiness} from './readiness';
import type {ReadinessBaseline, ReadinessResult} from './readiness';
import type {RunOrder} from './order';

type Phase = 'conditions' | 'load' | 'inference' | 'release';
type CleanupError = {phase: 'release'; message: string};

/** Random per JS process (not a device identifier): separates process restarts in records. */
export const PROCESS_INSTANCE_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
let modelLoadsInProcess = 0;

export interface InstanceIdentity {
  /** Every measured request in this protocol is the first on a freshly loaded model instance. */
  kind: 'fresh-instance';
  request: 'first-request-on-instance';
  processInstanceId: string;
  /** Loads already attempted in this JS process (0 = first model load since process start). */
  modelLoadsBeforeInProcess: number;
}

export interface AttemptConditions {
  before: ConditionSnapshot;
  beforeAssessment: ConditionAssessment;
  /** null when the repetition was skipped or aborted before sampling could start. */
  during: MonitorResult | null;
  duringAssessment: ConditionAssessment | null;
  after: ConditionSnapshot | null;
  afterAssessment: ConditionAssessment | null;
  /** This repetition's eligibility under CHARGING_POLICY (endpoints and every sample). */
  charging: ChargingStatus;
}

type AttemptBase = {
  runIndex: number;
  /** Fixed cooldown actually slept before this repetition's readiness check. */
  cooldownMs: number;
  readiness: ReadinessResult;
  instance: InstanceIdentity | null;
  conditions: AttemptConditions;
};

export type Attempt = AttemptBase & ({status: 'complete'} & SingleRunMetrics | {
  status: 'failed'; error: {phase: Phase; message: string}; metrics: SingleRunMetrics | null;
  cleanupError?: CleanupError;
} | {status: 'skipped'; reason: 'readiness-timeout' | 'charging'});

export type StopReason = 'repetition-failed' | 'readiness-timeout' | 'condition-probe-pending' |
  'cleanup-failed' | 'configuration-aborted' | 'setup-failed' | 'model-hash-mismatch';

/** Where the parsed configuration came from, and the SHA-256 of that file's bytes. */
export interface ConfigurationSource {
  /** bundled: configs/cpu-reference-v1.json in the APK; staged: xebench-config.json in the app's files dir. */
  kind: 'bundled' | 'staged';
  /** Staged: hash of the bytes read. Bundled: hash computed at build time; null if the build did not report it. */
  sha256: string | null;
}

export interface SessionIdentity {
  runId: string;
  timestampIso: string;
  deviceInfo: {model: string; androidRelease: string};
  /** null only for runs recorded as skipped or failed before the model was hashed. */
  artifact: {sha256: string; sizeBytes: number} | null;
  build: BuildIdentity;
  configurationSource: ConfigurationSource;
}

/** One engine report per model load, keyed by the repetition that loaded it. */
export interface ExecutionLoadReport extends ExecutionReport {
  runIndex: number;
}

const UNREPORTED: ExecutionReport = {gpu: null, reasonNoGPU: null, devices: null, androidLib: null, systemInfo: null};

export interface ConditionVerdict {
  /** pass: every planned repetition was ready and every endpoint/sample check passed with measured evidence. */
  status: 'pass' | 'fail' | 'unverified';
  reasons: string[];
  /** Publication eligibility under CHARGING_POLICY; independent of run validity. */
  charging: ChargingStatus;
}

export interface SessionContext {
  runOrder?: RunOrder | null;
  baseline?: ReadinessBaseline;
  now?: () => number;
}

export interface ReferenceSession extends Omit<SessionIdentity, 'artifact'> {
  schema: 'xebench-raw';
  schemaVersion: 1;
  harnessVersion: 'xebench-0.7-reference';
  /** Actual bytes hashed before measurement, and the configured expectation (null if none). */
  artifact: {sha256: string; sizeBytes: number; expectedSha256: string | null} | null;
  engine: string;
  engineVersion: string;
  backend: 'cpu';
  /** The requested backend is the backend field; execution is not verified, so this stays null (unknown). */
  observedBackend: null;
  /** Every parameter passed to the engine (an adapter that cannot state them cannot run this protocol). */
  effectiveSettings: LlamaRnEffectiveSettings;
  /** As reported by the engine after each successful load; reports are not verified execution. */
  executionReports: ExecutionLoadReport[];
  platform: 'android';
  model: string;
  quant: string;
  modelFile: string;
  configuration: ReferenceConfig;
  configurationRunId: string;
  runOrder: RunOrder | null;
  protocol: {
    version: 'cpu-reference-v3'; nColdRuns: number; cooldownSec: number; readinessTimeoutSec: number;
    /** Effective readiness rise bound (configuration override of the policy default). */
    maxBatteryTempRiseC: number;
    promptLabel: string; prompt: string; maxDecodeTokens: number;
    nThreads: number; nCtx: number; coldDefinition: string;
    measurementKind: 'fresh-instance-first-request'; warmRequests: 'not-measured';
    processRestartBetweenRepetitions: false;
  };
  status: 'running' | 'complete' | 'failed';
  repetitionsPlanned: number;
  stopReason: StopReason | null;
  coldRuns: Attempt[];
  summary: Record<'loadMs' | 'ttftMs' | 'prefillTps' | 'decodeTps', Aggregate | null>;
  conditionPolicy: typeof CONDITION_POLICY;
  chargingPolicy: typeof CHARGING_POLICY;
  conditionVerdict: ConditionVerdict;
  guardsPassed: false | null;
  guardNotes: string[];
}

const BACKEND_NOTE = 'Actual backend execution has not been verified';

/** Linear interpolation at (n-1)*p; publish this definition with the statistic. */
export function aggregate(values: number[]): Aggregate | null {
  if (!values.length) {
    return null;
  }
  if (values.some(value => !Number.isFinite(value) || value < 0)) {
    throw new Error('Metrics must be finite and non-negative');
  }
  const sorted = [...values].sort((a, b) => a - b);
  const percentile = (p: number) => {
    const index = (sorted.length - 1) * p;
    const lo = Math.floor(index);
    return sorted[lo] + (sorted[Math.ceil(index)] - sorted[lo]) * (index - lo);
  };
  return {median: percentile(0.5), iqr: percentile(0.75) - percentile(0.25),
    min: sorted[0], max: sorted[sorted.length - 1], n: sorted.length};
}

function validateMetrics(metrics: SingleRunMetrics) {
  for (const key of ['loadMs', 'ttftMs', 'prefillTps', 'decodeTps', 'promptTokens', 'decodeTokens'] as const) {
    aggregate([metrics[key]]);
  }
  if (metrics.engineInternal) {
    aggregate(Object.values(metrics.engineInternal));
    if (metrics.engineInternal.predictedMs <= 0) {
      throw new Error('Decode measurement requires positive native decode duration');
    }
  }
  if (metrics.decodeTps <= 0) {
    throw new Error('Decode measurement requires positive throughput');
  }
  if (!Number.isInteger(metrics.promptTokens) || metrics.promptTokens < 1 ||
      !Number.isInteger(metrics.decodeTokens) || metrics.decodeTokens < 1) {
    throw new Error('Token counts must be positive integers');
  }
}

/** Unknown evidence or missing repetitions can never produce a pass. */
export function conditionVerdict(record: Pick<ReferenceSession, 'coldRuns' | 'repetitionsPlanned'>): ConditionVerdict {
  const reasons = new Set<string>();
  let unverified = record.coldRuns.length < record.repetitionsPlanned;
  const power = emptyChargingCounts();
  for (const attempt of record.coldRuns) {
    const {readiness, conditions} = attempt;
    // A timeout caused only by unknown evidence or charging (eligibility, not validity) is unverified.
    const unknownOnly = readiness.reasons.every(reason =>
      reason === 'probe-incomplete' || reason === 'charging' || reason.startsWith('unknown-'));
    if (readiness.status === 'timeout' && !unknownOnly) reasons.add('readiness-timeout');
    if (readiness.status === 'probe-pending' || (conditions.after && probePending(conditions.after)) ||
        (conditions.during && monitorPending(conditions.during))) reasons.add('condition-probe-pending');
    if (attempt.status !== 'complete') unverified = true;
    for (const assessment of [conditions.beforeAssessment, conditions.duringAssessment, conditions.afterAssessment]) {
      if (!assessment || assessment.status === 'unverified') unverified = true;
      else if (assessment.status === 'fail') assessment.reasons.forEach(reason => reasons.add(reason));
    }
    const counts = chargingCounts(conditions, attempt.status !== 'skipped');
    for (const key of Object.keys(power) as (keyof ChargingCounts)[]) power[key] += counts[key];
  }
  return {status: reasons.size ? 'fail' : unverified || !record.coldRuns.length ? 'unverified' : 'pass',
    reasons: [...reasons], charging: assessCharging(power)};
}

/** Charging observations of one repetition; a measured one must have been sampled throughout. */
export function chargingCounts(conditions: Pick<AttemptConditions, 'before' | 'during' | 'after'>, measured: boolean) {
  const counts = emptyChargingCounts();
  for (const snapshot of [conditions.before, conditions.after]) {
    if (snapshot) counts[chargingState(snapshot)]++;
  }
  if (conditions.during) {
    for (const key of Object.keys(counts) as (keyof ChargingCounts)[]) counts[key] += conditions.during.summary.chargingStates[key];
  }
  // An unsampled stretch (no monitor, expiry, gaps, dropped ticks) is an unknown observation.
  if (measured && !(conditions.during && monitorCovered(conditions.during))) counts.unknown++;
  return counts;
}

function updateVerdict(record: ReferenceSession) {
  record.conditionVerdict = conditionVerdict(record);
  record.guardsPassed = record.conditionVerdict.status === 'fail' ? false : null;
  record.guardNotes = [BACKEND_NOTE, ...record.conditionVerdict.status === 'fail' ? record.conditionVerdict.reasons :
    record.conditionVerdict.status === 'unverified' ? ['Run conditions not fully verified'] : []];
}

/** Session shell shared by measured runs and runs recorded as skipped/failed before measurement. */
export function createSession(
  adapter: EngineAdapter, config: ReferenceConfig, run: ReferenceRun, identity: SessionIdentity,
  runOrder: RunOrder | null = null,
): ReferenceSession {
  if (!adapter.effectiveSettings) {
    throw new Error('Adapter cannot state its effective engine settings');
  }
  const record: ReferenceSession = {
    ...identity,
    artifact: identity.artifact && {...identity.artifact, expectedSha256: run.modelSha256 ?? null},
    schema: 'xebench-raw', schemaVersion: 1, harnessVersion: 'xebench-0.7-reference',
    engine: adapter.engine, engineVersion: adapter.engineVersion(), backend: 'cpu', observedBackend: null,
    effectiveSettings: adapter.effectiveSettings({maxDecodeTokens: config.maxDecodeTokens}), executionReports: [],
    platform: 'android', model: run.model, quant: run.quant, modelFile: run.modelFile,
    configuration: config, configurationRunId: run.id, runOrder,
    protocol: {
      version: 'cpu-reference-v3', nColdRuns: config.nColdRuns, cooldownSec: config.cooldownSec,
      readinessTimeoutSec: config.readinessTimeoutSec, maxBatteryTempRiseC: config.maxBatteryTempRiseC,
      promptLabel: STANDARD_PROMPT_LABEL, prompt: STANDARD_PROMPT, maxDecodeTokens: config.maxDecodeTokens,
      nThreads: run.nThreads, nCtx: run.nCtx,
      coldDefinition: 'fresh engine instance in warm app process; artifact hashing warms OS file cache',
      measurementKind: 'fresh-instance-first-request', warmRequests: 'not-measured',
      processRestartBetweenRepetitions: false,
    },
    status: 'running', repetitionsPlanned: config.nColdRuns, stopReason: null, coldRuns: [],
    summary: {loadMs: null, ttftMs: null, prefillTps: null, decodeTps: null},
    conditionPolicy: CONDITION_POLICY, chargingPolicy: CHARGING_POLICY,
    conditionVerdict: {status: 'unverified', reasons: [], charging: 'unknown'},
    guardsPassed: null, guardNotes: [],
  };
  updateVerdict(record);
  return record;
}

export const UNMEASURED_NOTES = {
  'configuration-aborted': 'Not run: an earlier run aborted the configuration',
  'setup-failed': 'Not run: the model artifact could not be read',
  'model-hash-mismatch': 'Not run: the model artifact does not match the configured SHA-256',
} as const;

/**
 * A run that never reached measurement (aborted configuration, unreadable model, or a
 * model whose bytes differ from the configured hash; only the last keeps its artifact).
 */
export function unmeasuredSession(
  adapter: EngineAdapter, config: ReferenceConfig, run: ReferenceRun, identity: SessionIdentity,
  runOrder: RunOrder | null, stopReason: keyof typeof UNMEASURED_NOTES,
): ReferenceSession {
  const record = createSession(adapter, config, run,
    {...identity, artifact: stopReason === 'model-hash-mismatch' ? identity.artifact : null}, runOrder);
  record.status = 'failed';
  record.stopReason = stopReason;
  record.guardNotes = [...record.guardNotes, UNMEASURED_NOTES[stopReason]];
  return record;
}

const message = (failure: unknown) => failure instanceof Error ? failure.message : String(failure);

export async function runSession(
  adapter: EngineAdapter, config: ReferenceConfig, run: ReferenceRun, identity: SessionIdentity,
  persist: (record: ReferenceSession) => Promise<void>,
  sleep: (ms: number) => Promise<void> = ms => new Promise(resolve => setTimeout(resolve, ms)),
  context: SessionContext = {},
): Promise<ReferenceSession> {
  const settings = adapter.settings();
  if (adapter.engine !== run.engine || adapter.backend !== 'cpu' ||
      settings.nThreads !== run.nThreads || settings.nCtx !== run.nCtx) {
    throw new Error('Adapter does not match the configured CPU reference run');
  }
  const record = createSession(adapter, config, run, identity, context.runOrder ?? null);
  const baseline = context.baseline ?? {batteryTempC: null};
  for (let runIndex = 0; runIndex < config.nColdRuns; runIndex++) {
    // The protocol cooldown precedes every repetition, the first included (after hashing).
    const cooldownMs = config.cooldownSec * 1000;
    if (cooldownMs) await sleep(cooldownMs);
    const {result: readiness, snapshot: before} = await awaitReadiness(config.readinessTimeoutSec, baseline, sleep, context.now,
      config.maxBatteryTempRiseC);
    const beforeAssessment = assessConditions(before);
    const base = {runIndex, cooldownMs, readiness, instance: null as InstanceIdentity | null};
    if (readiness.status === 'timeout') {
      // Never measure without measured readiness; keep the evidence for why it was skipped.
      record.coldRuns.push({...base, status: 'skipped',
        reason: readiness.reasons.includes('charging') ? 'charging' : 'readiness-timeout', conditions: {
          before, beforeAssessment, during: null, duringAssessment: null, after: null, afterAssessment: null,
          charging: assessCharging(chargingCounts({before, during: null, after: null}, false))}});
      record.status = 'failed';
      record.stopReason = 'readiness-timeout';
      updateVerdict(record);
      await persist(record);
      break;
    }
    let after: ConditionSnapshot | null = null;
    let during: MonitorResult | null = null;
    let monitorStart: Awaited<ReturnType<typeof startMonitor>> | null = null;
    let phase: Phase = 'conditions';
    let metrics: SingleRunMetrics | null = null;
    let error: {phase: Phase; message: string} | null = null;
    let cleanupError: CleanupError | null = null;
    try {
      if (readiness.status === 'probe-pending') throw new Error('Condition probe still running');
      monitorStart = await startMonitor();
      if (monitorStart === 'busy' || monitorStart === 'timeout') throw new Error('Condition monitor busy or unresponsive');
      phase = 'load';
      base.instance = {kind: 'fresh-instance', request: 'first-request-on-instance',
        processInstanceId: PROCESS_INSTANCE_ID, modelLoadsBeforeInProcess: modelLoadsInProcess++};
      const loadMs = await adapter.load();
      record.executionReports.push({runIndex, ...(adapter.executionReport?.() ?? UNREPORTED)});
      phase = 'inference';
      metrics = {...await adapter.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: config.maxDecodeTokens}), loadMs};
      validateMetrics(metrics);
    } catch (failure) {
      error = {phase, message: message(failure)};
      // Never serialize NaN/Infinity as misleading JSON null metrics.
      metrics = null;
    } finally {
      // Stop sampling first (also attempts cleanup of a late-started monitor), then observe
      // the loaded model before release; endpoint PSS is not a peak-memory sample.
      if (monitorStart === 'started' || monitorStart === 'timeout') during = await stopMonitor();
      else if (monitorStart !== null) during = normalizeMonitor(null, monitorStart);
      if (monitorStart === 'timeout' && during && !monitorPending(during)) during = {...during, status: 'timeout'};
      // Never probe beside a monitor that may still be running natively.
      if (!probePending(before) && !(during && monitorPending(during))) after = await readConditions();
      if ((after && probePending(after)) || (during && monitorPending(during))) {
        error = error ?? {phase: 'conditions', message: 'Condition probe still running'};
      }
      try {
        await adapter.release();
      } catch (failure) {
        cleanupError = {phase: 'release', message: message(failure)};
        error = error ?? cleanupError;
      }
    }
    const conditions: AttemptConditions = {before, beforeAssessment, during,
      duringAssessment: during && after ? assessDuring(during, before, after) : null,
      after, afterAssessment: after ? assessConditions(after) : null,
      charging: assessCharging(chargingCounts({before, during, after}, true))};
    record.coldRuns.push(error ? {...base, conditions, status: 'failed', error, metrics,
      ...(cleanupError ? {cleanupError} : {})} :
      {...base, conditions, status: 'complete', ...metrics!});
    const complete = record.coldRuns.filter((item): item is Attempt & SingleRunMetrics => item.status === 'complete');
    for (const metric of ['loadMs', 'ttftMs', 'prefillTps', 'decodeTps'] as const) {
      record.summary[metric] = aggregate(complete.map(item => item[metric]));
    }
    const pending = probePending(before) || (after !== null && probePending(after)) ||
      (during !== null && monitorPending(during));
    record.status = error ? 'failed' : runIndex === config.nColdRuns - 1 ? 'complete' : 'running';
    record.stopReason = cleanupError ? 'cleanup-failed' : pending ? 'condition-probe-pending' :
      error ? 'repetition-failed' : null;
    updateVerdict(record);
    // Each checkpoint is persisted before cooldown or the next native call.
    await persist(record);
    if (cleanupError) {
      // A resident context can contaminate every later model in the configuration.
      // Keep both errors in evidence before propagating this fatal cleanup failure.
      throw new Error(`Engine cleanup failed: ${cleanupError.message}`);
    }
    if (pending) {
      // A late native probe or monitor could overlap later timed work.
      throw new Error('Condition probe did not finish; configuration aborted');
    }
    if (error) {
      break;
    }
  }
  return record;
}
