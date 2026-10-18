/**
 * accelerator-v2 (accelerator-v1 plus build/execution identity): LiteRT-LM and llama.cpp on CPU, GPU and NPU, with an output-correctness
 * gate and observed-backend evidence. Repetitions, readiness, sampling, cooldown, run
 * order, the condition verdict and the build/configuration/engine identity evidence are those
 * of cpu-reference-v3 (session.ts).
 * Design and result template: docs/experiments/litert-accelerators.md.
 */
import type {EngineAdapter, LlamaRnEffectiveSettings} from './adapters/EngineAdapter';
import type {AcceleratorConfig, AcceleratorRun} from './config';
import {ACCELERATOR_PROMPT, ACCELERATOR_PROMPT_LABEL, chatTurn} from './prompts';
import type {ChatTemplate} from './prompts';
import {CHARGING_POLICY, CONDITION_POLICY} from './conditions';
import {
  Attempt, BACKEND_NOTE, conditionVerdict, ConditionVerdict, ExecutionLoadReport, noteModelLoad, runRepetitions,
  SessionContext, SessionIdentity, StopReason,
} from './session';
import type {ChargingPolicy} from './conditions';
import type {Aggregate} from './types';
import type {RunOrder} from './order';
import type {ReadinessBaseline} from './readiness';
import {BackendEvidence, summarizeBackend} from './backend';
import {
  candidateCorrectness, comparisonStatus, Comparison, Correctness, GATE_VERSION, referenceCorrectness, runGate,
} from './gate';

export const ACCELERATOR_COLD_DEFINITION =
  'fresh engine instance in warm app process; artifact hashing and the correctness gate warm OS file and engine caches';

export const ACCELERATOR_UNMEASURED_NOTES = {
  'configuration-aborted': 'Not run: an earlier run aborted the configuration',
  'setup-failed': 'Not run: the model artifact could not be read',
  'engine-unavailable': 'Not run: the engine is not linked in this build',
  'gate-cleanup-failed': 'Not run: the correctness gate could not release the engine',
  'model-hash-mismatch': 'Not run: the model artifact does not match the configured SHA-256',
} as const;
export type UnmeasuredReason = keyof typeof ACCELERATOR_UNMEASURED_NOTES;

export interface AcceleratorSession extends Omit<SessionIdentity, 'artifact'> {
  /** Bytes hashed before measurement, and the configured run's modelSha256 (null if none). */
  artifact: {sha256: string; sizeBytes: number; expectedSha256: string | null} | null;
  schema: 'xebench-raw';
  schemaVersion: 1;
  harnessVersion: 'xebench-0.8-accelerator';
  engine: string;
  engineVersion: string;
  /** Requested backend. */
  backend: AcceleratorRun['backend'];
  /** Summary of per-repetition evidence; null until a repetition has loaded the engine. */
  observedBackend: ReturnType<typeof summarizeBackend>;
  /** llama.cpp runs: every parameter passed to llama.rn (as cpu-reference-v3). LiteRT-LM: null. */
  effectiveSettings: LlamaRnEffectiveSettings | null;
  /** llama.rn's report after each successful load; LiteRT-LM reports none (empty). */
  executionReports: ExecutionLoadReport[];
  platform: 'android';
  model: string;
  quant: string;
  modelFile: string;
  purpose: AcceleratorRun['purpose'];
  configuration: AcceleratorConfig;
  configurationRunId: string;
  runOrder: RunOrder | null;
  protocol: {
    version: 'accelerator-v2'; nColdRuns: number; cooldownSec: number; readinessTimeoutSec: number;
    maxBatteryTempRiseC: number;
    promptLabel: string; prompt: string; promptFormat: 'user-turn-chat-template'; chatTemplate: ChatTemplate;
    maxDecodeTokens: number; sampling: 'greedy'; nThreads: number | null; nCtx: number | null;
    coldDefinition: string; measurementKind: 'fresh-instance-first-request'; warmRequests: 'not-measured';
    processRestartBetweenRepetitions: false; gate: typeof GATE_VERSION;
  };
  status: 'running' | 'complete' | 'failed';
  repetitionsPlanned: number;
  stopReason: StopReason | null;
  coldRuns: Attempt[];
  summary: Record<'loadMs' | 'ttftMs' | 'prefillTps' | 'decodeTps', Aggregate | null>;
  conditionPolicy: typeof CONDITION_POLICY;
  chargingPolicy: ChargingPolicy;
  conditionVerdict: ConditionVerdict;
  guardsPassed: false | null;
  guardNotes: string[];
  /**
   * Backend evidence from the gate's load, the first load of this run in the process. For
   * LiteRT-LM it is the only load that can show the accelerator runtime being loaded; later
   * repetitions find it already mapped. null when the gate did not load the engine.
   */
  gateBackend: BackendEvidence | null;
  correctness: Correctness;
  comparison: Comparison;
}

/** What the correctness gate decided for a run before it was measured. */
export interface SessionGate {
  correctness: Correctness;
  gateBackend: BackendEvidence | null;
}

export function updateAcceleratorVerdict(record: AcceleratorSession) {
  const evidence = record.coldRuns.flatMap(attempt =>
    attempt.observedBackend ? [attempt.observedBackend as BackendEvidence] : []);
  record.observedBackend = summarizeBackend(evidence);
  record.conditionVerdict = conditionVerdict(record);
  record.guardsPassed = record.conditionVerdict.status === 'fail' ? false : null;
  const verdict = record.conditionVerdict;
  record.guardNotes = [...record.observedBackend?.status === 'confirmed' ? [] : [BACKEND_NOTE],
    ...verdict.status === 'fail' ? verdict.reasons : verdict.status === 'unverified' ? ['Run conditions not fully verified'] : []];
  if (record.stopReason && record.stopReason in ACCELERATOR_UNMEASURED_NOTES) {
    record.guardNotes.push(ACCELERATOR_UNMEASURED_NOTES[record.stopReason as UnmeasuredReason]);
  }
  record.comparison = comparisonStatus(record.purpose, record.correctness,
    record.observedBackend?.status === 'mismatch' || record.gateBackend?.status === 'mismatch');
}

/** Correctness of a run whose gate never ran. */
export function gateNotRun(run: AcceleratorRun, config: AcceleratorConfig): Correctness {
  const reference = config.runs.find(item => item.id === run.referenceRunId);
  return {version: GATE_VERSION, role: run.backend === 'cpu' ? 'reference' : 'candidate', status: 'unverified',
    reasons: ['gate-not-run'], error: null, probes: [],
    reference: reference ? {configurationRunId: reference.id, backend: 'cpu', modelFile: reference.modelFile,
      artifactSha256: null, sameArtifact: null, status: null} : null};
}

export function createAcceleratorSession(adapter: Pick<EngineAdapter, 'engine' | 'engineVersion' | 'effectiveSettings'> | null,
  config: AcceleratorConfig, run: AcceleratorRun, identity: SessionIdentity, runOrder: RunOrder | null,
  gate: SessionGate): AcceleratorSession {
  const record: AcceleratorSession = {
    ...identity, artifact: identity.artifact && {...identity.artifact, expectedSha256: run.modelSha256 ?? null},
    schema: 'xebench-raw', schemaVersion: 1, harnessVersion: 'xebench-0.8-accelerator',
    effectiveSettings: adapter?.effectiveSettings ? adapter.effectiveSettings({maxDecodeTokens: config.maxDecodeTokens}) :
      null,
    executionReports: [],
    engine: run.engine, engineVersion: adapter ? adapter.engineVersion() : 'unavailable', backend: run.backend,
    observedBackend: null, platform: 'android', model: run.model, quant: run.quant, modelFile: run.modelFile,
    purpose: run.purpose, configuration: config, configurationRunId: run.id, runOrder,
    protocol: {
      version: 'accelerator-v2', nColdRuns: config.nColdRuns, cooldownSec: config.cooldownSec,
      readinessTimeoutSec: config.readinessTimeoutSec, maxBatteryTempRiseC: config.maxBatteryTempRiseC,
      promptLabel: ACCELERATOR_PROMPT_LABEL,
      prompt: ACCELERATOR_PROMPT, promptFormat: 'user-turn-chat-template', chatTemplate: config.chatTemplate,
      maxDecodeTokens: config.maxDecodeTokens, sampling: 'greedy', nThreads: run.nThreads, nCtx: run.nCtx,
      coldDefinition: ACCELERATOR_COLD_DEFINITION, measurementKind: 'fresh-instance-first-request',
      warmRequests: 'not-measured', processRestartBetweenRepetitions: false, gate: GATE_VERSION,
    },
    status: 'running', repetitionsPlanned: config.nColdRuns, stopReason: null, coldRuns: [],
    summary: {loadMs: null, ttftMs: null, prefillTps: null, decodeTps: null},
    conditionPolicy: CONDITION_POLICY, chargingPolicy: CHARGING_POLICY,
    conditionVerdict: {status: 'unverified', reasons: [], charging: 'unknown'},
    guardsPassed: null, guardNotes: [], gateBackend: gate.gateBackend, correctness: gate.correctness,
    comparison: {status: 'unverified', reasons: []},
  };
  updateAcceleratorVerdict(record);
  return record;
}

export function unmeasuredAcceleratorSession(adapter: Pick<EngineAdapter, 'engine' | 'engineVersion' | 'effectiveSettings'> | null,
  config: AcceleratorConfig, run: AcceleratorRun, identity: SessionIdentity, runOrder: RunOrder | null,
  stopReason: UnmeasuredReason, gate: SessionGate = {correctness: gateNotRun(run, config), gateBackend: null},
): AcceleratorSession {
  // Without a session artifact there is nothing to compare the reference artifact with.
  const reference = gate.correctness.reference;
  const correctness = reference ? {...gate.correctness, reference: {...reference, sameArtifact: null}} : gate.correctness;
  // A hash mismatch keeps the bytes it hashed, so the record shows what was found.
  const record = createAcceleratorSession(adapter, config, run,
    {...identity, artifact: stopReason === 'model-hash-mismatch' ? identity.artifact : null}, runOrder,
    {...gate, correctness});
  record.status = 'failed';
  record.stopReason = stopReason;
  updateAcceleratorVerdict(record);
  return record;
}

/** Measure one configured run; its correctness was decided by the gate beforehand. */
export async function runAcceleratorSession(
  adapter: EngineAdapter, config: AcceleratorConfig, run: AcceleratorRun, identity: SessionIdentity,
  gate: SessionGate, persist: (record: AcceleratorSession) => Promise<void>,
  sleep?: (ms: number) => Promise<void>, context: SessionContext = {},
): Promise<AcceleratorSession> {
  const settings = adapter.settings();
  if (adapter.engine !== run.engine || adapter.backend !== run.backend ||
      settings.nThreads !== run.nThreads || settings.nCtx !== run.nCtx) {
    throw new Error('Adapter does not match the configured accelerator run');
  }
  const record = createAcceleratorSession(adapter, config, run, identity, context.runOrder ?? null, gate);
  return runRepetitions(record, adapter, {
    nColdRuns: config.nColdRuns, cooldownSec: config.cooldownSec, readinessTimeoutSec: config.readinessTimeoutSec,
    maxBatteryTempRiseC: config.maxBatteryTempRiseC,
    bench: {prompt: chatTurn(config.chatTemplate, ACCELERATOR_PROMPT), text: ACCELERATOR_PROMPT,
      maxDecodeTokens: config.maxDecodeTokens},
    update: updateAcceleratorVerdict, executionReports: 'when-reported',
    observe: () => adapter.backendEvidence ? adapter.backendEvidence() :
      {status: 'unknown', source: 'none', report: null, libraries: null},
  }, persist, sleep, context);
}

export interface AcceleratorIO {
  /** Hash the run's model file; throws when it cannot be read. */
  artifact(run: AcceleratorRun): Promise<{sha256: string; sizeBytes: number}>;
  /** A fresh adapter for the run; throws when the engine is not linked in this build. */
  adapter(run: AcceleratorRun): EngineAdapter;
  identity(): SessionIdentity;
  persister(run: AcceleratorRun, identity: SessionIdentity): (record: AcceleratorSession) => Promise<void>;
  log(message: string): void;
  sleep?: (ms: number) => Promise<void>;
  /** Readiness clock (tests pin it). */
  now?: () => number;
}

type GateOutcome = SessionGate & {releaseFailed: boolean};

/**
 * Whole configuration: gate every CPU run first (the references), then for each run in
 * seeded order gate it (GPU/NPU) and measure it. A gate failure never skips measurement;
 * it marks the session invalid for comparison. Engine cleanup failures stop the
 * configuration, and every later run is still recorded as not run.
 */
export async function runAcceleratorConfiguration(config: AcceleratorConfig, runs: AcceleratorRun[],
  runOrderFor: (run: AcceleratorRun, position: number) => RunOrder, io: AcceleratorIO) {
  const artifacts = new Map<string, {sha256: string; sizeBytes: number} | null>();
  const artifactOf = async (run: AcceleratorRun) => {
    if (!artifacts.has(run.modelFile)) {
      io.log(`${run.id}: hashing model…`);
      artifacts.set(run.modelFile, await io.artifact(run).catch(() => null));
    }
    return artifacts.get(run.modelFile)!;
  };
  /** A configured modelSha256 that the staged file does not match: never gated or measured. */
  const mismatched = (run: AcceleratorRun, artifact: {sha256: string}) =>
    run.modelSha256 !== undefined && artifact.sha256 !== run.modelSha256;
  const adapterOf = (run: AcceleratorRun) => {
    try {
      return io.adapter(run);
    } catch {
      return null;
    }
  };
  const gate = async (adapter: EngineAdapter, run: AcceleratorRun, reference: GateOutcome | undefined,
    artifact: {sha256: string} | null): Promise<GateOutcome> => {
    io.log(`${run.id}: correctness gate…`);
    const result = await runGate(adapter, config.maxDecodeTokens, noteModelLoad, config.chatTemplate);
    if (run.backend === 'cpu') {
      return {correctness: referenceCorrectness(config.maxDecodeTokens, result), gateBackend: result.backend,
        releaseFailed: result.releaseFailed};
    }
    const referenceRun = config.runs.find(item => item.id === run.referenceRunId)!;
    const referenceSha = artifacts.get(referenceRun.modelFile)?.sha256 ?? null;
    return {correctness: candidateCorrectness(config.maxDecodeTokens, result, reference?.correctness ?? null, {
      configurationRunId: referenceRun.id, backend: 'cpu', modelFile: referenceRun.modelFile,
      artifactSha256: referenceSha,
      sameArtifact: referenceSha !== null && artifact !== null ? referenceSha === artifact.sha256 : null,
    }), gateBackend: result.backend, releaseFailed: result.releaseFailed};
  };

  const gates = new Map<string, GateOutcome>();
  // Shared across the configuration so later runs must return near the first ready temperature.
  const baseline: ReadinessBaseline = {batteryTempC: null};
  let fatal: Error | null = null;
  // References first, in configuration order, so every candidate can be compared.
  for (const run of config.runs.filter(item => item.backend === 'cpu')) {
    const adapter = adapterOf(run);
    const hashed = adapter ? await artifactOf(run) : null;
    if (!adapter || !hashed || mismatched(run, hashed)) continue;
    const outcome = await gate(adapter, run, undefined, artifacts.get(run.modelFile)!);
    gates.set(run.id, outcome);
    io.log(`${run.id}: reference gate ${outcome.correctness.status}`);
    if (outcome.releaseFailed) {
      fatal = new Error(`Engine cleanup failed in the correctness gate of ${run.id}`);
      break;
    }
  }

  for (const [position, run] of runs.entries()) {
    const identity = io.identity();
    const persist = io.persister(run, identity);
    const runOrder = runOrderFor(run, position);
    const adapter = adapterOf(run);
    const recordUnmeasured = async (reason: UnmeasuredReason, decided?: SessionGate,
      hashed: SessionIdentity['artifact'] = null) => {
      await persist(unmeasuredAcceleratorSession(adapter, config, run, {...identity, artifact: hashed}, runOrder, reason,
        decided));
      console.log(`XEBENCH_ERROR ${run.id}: ${reason}`);
    };
    const earlier = gates.get(run.id);
    if (fatal) {
      try {
        if (earlier?.releaseFailed) await recordUnmeasured('gate-cleanup-failed', earlier);
        else await recordUnmeasured('configuration-aborted');
      } catch {
        break;
      }
      continue;
    }
    try {
      if (!adapter) {
        await recordUnmeasured('engine-unavailable');
        continue;
      }
      const artifact = await artifactOf(run);
      if (!artifact) {
        await recordUnmeasured('setup-failed');
        continue;
      }
      if (mismatched(run, artifact)) {
        await recordUnmeasured('model-hash-mismatch', undefined, artifact);
        continue;
      }
      let outcome = gates.get(run.id);
      if (!outcome) {
        outcome = await gate(adapter, run, run.referenceRunId ? gates.get(run.referenceRunId) : undefined, artifact);
        io.log(`${run.id}: gate ${outcome.correctness.status}`);
      }
      if (outcome.releaseFailed) {
        fatal = new Error(`Engine cleanup failed in the correctness gate of ${run.id}`);
        await recordUnmeasured('gate-cleanup-failed', outcome);
        continue;
      }
      const result = await runAcceleratorSession(adapter, config, run, {...identity, artifact}, outcome,
        persist, io.sleep, {runOrder, baseline, ...(io.now ? {now: io.now} : {})});
      if (result.status === 'failed') {
        console.log(`XEBENCH_ERROR ${run.id}: see session evidence`);
      }
    } catch (failure) {
      // Cleanup, probe or persistence failures make later native work unsafe or unrecordable.
      fatal = failure instanceof Error ? failure : new Error(String(failure));
    }
  }
  if (fatal) {
    throw fatal;
  }
}
