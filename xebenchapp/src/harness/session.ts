import type {EngineAdapter} from './adapters/EngineAdapter';
import type {Aggregate, SingleRunMetrics} from './types';
import type {ReferenceConfig, ReferenceRun} from './config';
import {STANDARD_PROMPT, STANDARD_PROMPT_LABEL} from './prompts';

type Phase = 'load' | 'inference' | 'release';
type Attempt = ({status: 'complete'} & SingleRunMetrics | {
  status: 'failed'; error: {phase: Phase; message: string}; metrics: SingleRunMetrics | null;
}) & {runIndex: number};

export interface SessionIdentity {
  runId: string;
  timestampIso: string;
  deviceInfo: {model: string; androidRelease: string};
  artifact: {sha256: string; sizeBytes: number};
}

export interface ReferenceSession extends SessionIdentity {
  schema: 'xebench-raw';
  schemaVersion: 1;
  harnessVersion: 'xebench-0.5-reference';
  engine: string;
  engineVersion: string;
  backend: 'cpu';
  observedBackend: null;
  platform: 'android';
  model: string;
  quant: string;
  modelFile: string;
  configuration: ReferenceConfig;
  configurationRunId: string;
  protocol: {
    version: 'cpu-reference-v1'; nColdRuns: number; cooldownSec: number;
    promptLabel: string; prompt: string; maxDecodeTokens: number;
    nThreads: number; nCtx: number; coldDefinition: string;
  };
  status: 'running' | 'complete' | 'failed';
  coldRuns: Attempt[];
  summary: Record<'loadMs' | 'ttftMs' | 'prefillTps' | 'decodeTps', Aggregate | null>;
  guardsPassed: null;
  guardNotes: string[];
}

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
  }
  if (!Number.isInteger(metrics.promptTokens) || metrics.promptTokens < 1 ||
      !Number.isInteger(metrics.decodeTokens) || metrics.decodeTokens < 1) {
    throw new Error('Token counts must be positive integers');
  }
}

export async function runSession(
  adapter: EngineAdapter, config: ReferenceConfig, run: ReferenceRun, identity: SessionIdentity,
  persist: (record: ReferenceSession) => Promise<void>,
  sleep: (ms: number) => Promise<void> = ms => new Promise(resolve => setTimeout(resolve, ms)),
): Promise<ReferenceSession> {
  const settings = adapter.settings();
  if (adapter.engine !== run.engine || adapter.backend !== 'cpu' ||
      settings.nThreads !== run.nThreads || settings.nCtx !== run.nCtx) {
    throw new Error('Adapter does not match the configured CPU reference run');
  }
  const record: ReferenceSession = {
    ...identity, schema: 'xebench-raw', schemaVersion: 1, harnessVersion: 'xebench-0.5-reference',
    engine: adapter.engine, engineVersion: adapter.engineVersion(), backend: 'cpu', observedBackend: null,
    platform: 'android', model: run.model, quant: run.quant, modelFile: run.modelFile,
    configuration: config, configurationRunId: run.id,
    protocol: {
      version: 'cpu-reference-v1', nColdRuns: config.nColdRuns, cooldownSec: config.cooldownSec,
      promptLabel: STANDARD_PROMPT_LABEL, prompt: STANDARD_PROMPT, maxDecodeTokens: config.maxDecodeTokens,
      nThreads: run.nThreads, nCtx: run.nCtx,
      coldDefinition: 'fresh engine instance in warm app process; artifact hashing warms OS file cache',
    },
    status: 'running', coldRuns: [], summary: {loadMs: null, ttftMs: null, prefillTps: null, decodeTps: null},
    guardsPassed: null, guardNotes: ['Run conditions and actual backend execution have not been verified'],
  };
  for (let runIndex = 0; runIndex < config.nColdRuns; runIndex++) {
    let phase: Phase = 'load';
    let metrics: SingleRunMetrics | null = null;
    let error: {phase: Phase; message: string} | null = null;
    try {
      const loadMs = await adapter.load();
      phase = 'inference';
      metrics = {...await adapter.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: config.maxDecodeTokens}), loadMs};
      validateMetrics(metrics);
    } catch (failure) {
      error = {phase, message: failure instanceof Error ? failure.message : String(failure)};
      // Never serialize NaN/Infinity as misleading JSON null metrics.
      metrics = null;
    } finally {
      try {
        await adapter.release();
      } catch (failure) {
        error = error ?? {phase: 'release', message: failure instanceof Error ? failure.message : String(failure)};
      }
    }
    record.coldRuns.push(error ? {runIndex, status: 'failed', error, metrics} :
      {runIndex, status: 'complete', ...metrics!});
    const complete = record.coldRuns.filter((item): item is Attempt & SingleRunMetrics => item.status === 'complete');
    for (const metric of ['loadMs', 'ttftMs', 'prefillTps', 'decodeTps'] as const) {
      record.summary[metric] = aggregate(complete.map(item => item[metric]));
    }
    record.status = error ? 'failed' : runIndex === config.nColdRuns - 1 ? 'complete' : 'running';
    // Each checkpoint is persisted before cooldown or the next native call.
    await persist(record);
    if (error) {
      break;
    }
    if (record.status === 'running') {
      await sleep(config.cooldownSec * 1000);
    }
  }
  return record;
}
