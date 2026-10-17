/**
 * Output-correctness gate (output-gate-v1). Before an accelerator session is measured,
 * the same engine and backend answer a fixed greedy probe set. The answers are checked
 * against expected keywords and compared with the CPU reference run of the same engine.
 * A failing session keeps its metrics but is invalid for comparison.
 *
 * Why: LiteRT-LM#3508. Google's SM8550 Gemma 3 1B NPU bundle produces wrong output for
 * prompts longer than one 128-token prefill chunk, while still reporting fast tok/s.
 * The long probes here span several prefill chunks, with the needed fact placed last,
 * first and in the middle. The first three are verbatim from the 2026-10-17 spike.
 *
 * Mirrored by scripts/raw_session.py (assess_probe / correctness_status); keep in step.
 */
import type {EngineAdapter} from './adapters/EngineAdapter';
import {ACCELERATOR_PROMPT, chatTurn} from './prompts';
import type {ChatTemplate} from './prompts';
import type {BackendEvidence} from './backend';

export const GATE_VERSION = 'output-gate-v1' as const;
export const ANSWER_MAX_TOKENS = 32;

const FILLER = ('The office opens at nine and closes at six on weekdays. Visitors sign ' +
  'in at the front desk and receive a badge. Meetings are held in the rooms on the ' +
  'second floor, which must be booked in advance through the calendar system. The ' +
  'kitchen is cleaned every evening, and staff are asked to label food in the fridge. ' +
  'Parking is available in the basement for employees with a permit. ').repeat(3);

export interface Probe {
  id: string;
  /** answer: pass/fail on an expected keyword. agreement: compared with the reference only. */
  kind: 'answer' | 'agreement';
  prompt: string;
  expected: string | null;
  maxOutputTokens: number;
}

export const PROBES: Probe[] = [
  {id: 'short-capital', kind: 'answer', expected: 'paris', maxOutputTokens: ANSWER_MAX_TOKENS,
    prompt: 'What is the capital of France? Answer with one word.'},
  {id: 'long-fact-last', kind: 'answer', expected: 'pelican', maxOutputTokens: ANSWER_MAX_TOKENS,
    prompt: FILLER + 'The secret code word is PELICAN. Question: what is the secret code word? ' +
      'Answer with the code word only.'},
  {id: 'long-fact-first', kind: 'answer', expected: 'pelican', maxOutputTokens: ANSWER_MAX_TOKENS,
    prompt: 'The secret code word is PELICAN. ' + FILLER + 'Question: what is the secret code word? ' +
      'Answer with the code word only.'},
  {id: 'long-fact-middle', kind: 'answer', expected: '417', maxOutputTokens: ANSWER_MAX_TOKENS,
    prompt: FILLER + 'The meeting room for the review is room 417. ' + FILLER +
      'Question: which room is the review in? Answer with the room number only.'},
  // The measured prompt itself, at the measured decode budget (set per session).
  {id: 'measured-prompt', kind: 'agreement', expected: null, maxOutputTokens: 0, prompt: ACCELERATOR_PROMPT},
];

export interface ProbeResult {
  id: string;
  kind: Probe['kind'];
  prompt: string;
  expected: string | null;
  maxOutputTokens: number;
  /** null when the gate failed before this probe produced output. */
  output: string | null;
  /** answer probes: expected keyword present; agreement probes: always null. */
  passed: boolean | null;
  /** The CPU reference's output for this probe; null for the reference session itself. */
  reference: {output: string | null; passed: boolean | null} | null;
  exactMatch: boolean | null;
  /** Shared leading Unicode code points with the reference output (after trim). */
  commonPrefixChars: number | null;
}

export type GateError = {phase: 'load' | 'inference' | 'release'; message: string};

export interface Correctness {
  version: typeof GATE_VERSION;
  /** reference: a CPU run whose outputs other runs are compared with. */
  role: 'reference' | 'candidate';
  status: 'pass' | 'fail' | 'unverified';
  reasons: string[];
  reference: null | {
    configurationRunId: string;
    backend: 'cpu';
    modelFile: string;
    artifactSha256: string | null;
    sameArtifact: boolean | null;
    /** The reference gate's status; null when the reference gate did not run. */
    status: Correctness['status'] | null;
  };
  error: GateError | null;
  probes: ProbeResult[];
}

/** Lowercase, collapse every non [a-z0-9] run to one space, pad: word-boundary keyword match. */
export const normalizeAnswer = (text: string) => ` ${text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()} `;

export const answerPassed = (output: string, expected: string) =>
  normalizeAnswer(output).includes(` ${expected} `);

export function commonPrefix(a: string, b: string): number {
  const x = Array.from(a.trim());
  const y = Array.from(b.trim());
  let index = 0;
  while (index < x.length && index < y.length && x[index] === y[index]) index++;
  return index;
}

export function probeResult(probe: Probe, output: string | null,
  reference: {output: string | null; passed: boolean | null} | null): ProbeResult {
  const passed = output === null || probe.kind !== 'answer' || probe.expected === null ? null :
    answerPassed(output, probe.expected);
  const compared = output !== null && reference !== null && reference.output !== null;
  return {id: probe.id, kind: probe.kind, prompt: probe.prompt, expected: probe.expected,
    maxOutputTokens: probe.maxOutputTokens, output, passed, reference,
    exactMatch: compared ? output!.trim() === reference!.output!.trim() : null,
    commonPrefixChars: compared ? commonPrefix(output!, reference!.output!) : null};
}

/** Status and reason codes, recomputed by the host validator from the probes. */
export function correctnessStatus(role: Correctness['role'], error: GateError | null, probes: ProbeResult[],
  referenceStatus: Correctness['status'] | null): Pick<Correctness, 'status' | 'reasons'> {
  if (!probes.length) return {status: 'unverified', reasons: ['gate-not-run']};
  const answers = probes.filter(probe => probe.kind === 'answer');
  // A wrong answer is decisive even when a later probe errored.
  if (answers.some(probe => probe.passed === false && (role === 'reference' || probe.reference?.passed === true))) {
    return {status: 'fail', reasons: ['answer-mismatch']};
  }
  if (error) return {status: 'unverified', reasons: ['gate-error']};
  if (role === 'reference') {
    return answers.some(probe => probe.passed !== true) ? {status: 'fail', reasons: ['answer-mismatch']} :
      {status: 'pass', reasons: []};
  }
  if (referenceStatus === null) return {status: 'unverified', reasons: ['reference-unavailable']};
  if (referenceStatus !== 'pass') return {status: 'unverified', reasons: ['reference-failed']};
  return answers.some(probe => probe.passed !== true || probe.reference?.passed !== true) ?
    {status: 'fail', reasons: ['answer-mismatch']} : {status: 'pass', reasons: []};
}

export interface Comparison {
  /** valid: usable in a cross-backend comparison; invalid metrics are kept but excluded. */
  status: 'valid' | 'invalid' | 'unverified';
  reasons: string[];
}

/** backendMismatch: the engine itself reported a backend other than the requested one. */
export function comparisonStatus(purpose: 'measurement' | 'bug-repro', correctness: Pick<Correctness, 'status'>,
  backendMismatch = false): Comparison {
  const reasons = [...purpose === 'bug-repro' ? ['bug-repro'] : [],
    ...correctness.status === 'fail' ? ['correctness-fail'] : correctness.status === 'unverified' ?
      ['correctness-unverified'] : [], ...backendMismatch ? ['backend-mismatch'] : []];
  const invalid = purpose === 'bug-repro' || correctness.status === 'fail' || backendMismatch;
  return {status: invalid ? 'invalid' : correctness.status === 'unverified' ? 'unverified' : 'valid', reasons};
}

/** The probe set with the measured prompt's decode budget filled in. */
export const probesFor = (maxDecodeTokens: number) =>
  PROBES.map(probe => probe.kind === 'agreement' ? {...probe, maxOutputTokens: maxDecodeTokens} : probe);

/**
 * Load a fresh instance, answer every probe greedily, release. Never throws for engine
 * failures (they become `error`); a release failure is reported so the caller can stop
 * the configuration, since a resident engine contaminates later runs.
 */
export interface GateResult {
  outputs: (string | null)[];
  error: GateError | null;
  releaseFailed: boolean;
  /** Backend evidence right after the gate's load: the first load of this run in the process. */
  backend: BackendEvidence | null;
}

export async function runGate(adapter: EngineAdapter, maxDecodeTokens: number, onLoad: () => void = () => {},
  template: ChatTemplate = 'gemma3'): Promise<GateResult> {
  const probes = probesFor(maxDecodeTokens);
  const outputs: (string | null)[] = probes.map(() => null);
  let error: GateError | null = null;
  let phase: GateError['phase'] = 'load';
  let backend: BackendEvidence | null = null;
  if (!adapter.generate) {
    return {outputs, error: {phase: 'load', message: 'Engine adapter cannot generate text'}, releaseFailed: false,
      backend};
  }
  try {
    onLoad();
    await adapter.load();
    backend = adapter.backendEvidence ? adapter.backendEvidence() : null;
    phase = 'inference';
    for (const [index, probe] of probes.entries()) {
      outputs[index] = await adapter.generate({prompt: chatTurn(template, probe.prompt), text: probe.prompt,
        maxDecodeTokens: probe.maxOutputTokens});
    }
  } catch (failure) {
    error = {phase, message: failure instanceof Error ? failure.message : String(failure)};
  }
  try {
    await adapter.release();
    return {outputs, error, releaseFailed: false, backend};
  } catch (failure) {
    return {outputs, error: error ?? {phase: 'release', message: failure instanceof Error ? failure.message :
      String(failure)}, releaseFailed: true, backend};
  }
}

export function referenceCorrectness(maxDecodeTokens: number, gate: {outputs: (string | null)[]; error: GateError | null}):
  Correctness {
  const probes = probesFor(maxDecodeTokens).map((probe, index) => probeResult(probe, gate.outputs[index], null));
  return {version: GATE_VERSION, role: 'reference', ...correctnessStatus('reference', gate.error, probes, null),
    reference: null, error: gate.error, probes};
}

export function candidateCorrectness(maxDecodeTokens: number, gate: {outputs: (string | null)[]; error: GateError | null},
  reference: Correctness | null, identity: Omit<NonNullable<Correctness['reference']>, 'status'>): Correctness {
  const probes = probesFor(maxDecodeTokens).map((probe, index) => {
    const ref = reference?.probes.find(item => item.id === probe.id);
    return probeResult(probe, gate.outputs[index], ref ? {output: ref.output, passed: ref.passed} : null);
  });
  return {version: GATE_VERSION, role: 'candidate',
    ...correctnessStatus('candidate', gate.error, probes, reference ? reference.status : null),
    reference: {...identity, status: reference ? reference.status : null}, error: gate.error, probes};
}
