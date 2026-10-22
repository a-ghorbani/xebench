/**
 * ExecuTorch record rules without native imports (ExecutorchAdapter uses them on device; tests and
 * the accelerator fixture use them directly). Mirrored by scripts/raw_session.py; keep in step.
 */
import type {SingleRunMetrics} from './types';
import type {ExecutorchEffectiveSettings, SettingsRequest} from './adapters/EngineAdapter';
import type {ProgramReport} from './backend';
import {NO_RUNTIME_VERSIONS, RuntimeVersions, version} from './runtime';

/** runtimeVersions from ExecutorchBenchModule's constants and react-native-executorch's package version. */
export function executorchRuntimeVersions(constants: unknown, packageVersion: unknown): RuntimeVersions {
  const value = (constants ?? {}) as Record<string, unknown>;
  return {...NO_RUNTIME_VERSIONS, executorch: version(value.executorchVersion),
    reactNativeExecutorch: version(packageVersion)};
}

/** Exported label alphabet: no commas (scripts/export_evidence.py LABEL). */
export const executorchEngineVersion = (versions: RuntimeVersions) =>
  `ExecuTorch ${versions.executorch ?? 'unknown'} + react-native-executorch ` +
  `${versions.reactNativeExecutorch ?? 'unknown'} (xnnpack)`;

/** Every parameter ExecutorchAdapter passes to react-native-executorch for a request. */
export function executorchEffectiveSettings(request: SettingsRequest,
  distribution: {version: string; nativeLibsVersion: unknown}): ExecutorchEffectiveSettings {
  return {binding: 'react-native-executorch', runner: 'createLLMRunner', load: {loadMode: 'mmap', modalities: []},
    generation: {temperature: 0, maxNewTokens: request.maxDecodeTokens, ignoreEos: request.decodeMode === 'fixed',
      numBos: 0, numEos: 0, echo: false},
    threads: 'runtime-default', unlisted: 'binding-defaults',
    distribution: {source: 'npm', package: 'react-native-executorch', version: distribution.version,
      nativeLibsVersion: version(distribution.nativeLibsVersion), backends: ['xnnpack'], patches: []}};
}

export const LIBRARY = /^lib[A-Za-z0-9_.+-]{1,120}\.so$/;
export const NAME = /^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,127}$/;

/** A sorted copy of a bounded string array whose items all match pattern; null otherwise. */
export const strings = (value: unknown, pattern: RegExp, max = 256): string[] | null =>
  Array.isArray(value) && value.length <= max && value.every(item => typeof item === 'string' && pattern.test(item)) ?
    [...value as string[]].sort() : null;

/** Bounded copy of loadModel(...).backends (method name -> delegates), sorted by method; null if malformed. */
export function programMethods(backends: unknown): ProgramReport['methods'] {
  if (!backends || typeof backends !== 'object' || Array.isArray(backends)) return null;
  const entries = Object.entries(backends as Record<string, unknown>);
  if (entries.length > 64 || entries.some(([name]) => !NAME.test(name))) return null;
  const methods = entries.map(([name, delegates]) => ({name, delegates: strings(delegates, NAME, 16)}));
  if (methods.some(method => method.delegates === null)) return null;
  return methods.map(method => ({name: method.name, delegates: method.delegates!}))
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** The token-input bound of `forward`: the last dimension of input 0 (a dynamic dimension's maximum). */
export function forwardTokenBound(schema: unknown): number | null {
  const forward = (schema as {forward?: {inputs?: unknown[]}} | null)?.forward;
  const input = forward?.inputs?.[0] as {kind?: unknown; shape?: unknown[]} | undefined;
  if (!input || input.kind !== 'Tensor' || !Array.isArray(input.shape) || input.shape.length < 2) return null;
  const dim = input.shape[input.shape.length - 1] as {kind?: unknown; value?: unknown; range?: {max?: unknown};
    choices?: unknown[]} | null;
  const choices = dim?.kind === 'enum' && Array.isArray(dim.choices) &&
    dim.choices.every(item => typeof item === 'number') ? dim.choices as number[] : null;
  const bound = dim?.kind === 'constant' ? dim.value : dim?.kind === 'range' ? dim.range?.max :
    choices && choices.length ? Math.max(...choices) : null;
  return typeof bound === 'number' && Number.isInteger(bound) && bound > 0 ? bound : null;
}

/** What one inspection of the .pte reports, before any timed load. */
export interface ProgramInspection {
  methods: ProgramReport['methods'];
  /** min(get_max_seq_len, forward's token bound): the most tokens one prefill forward takes. */
  prefillChunkTokens: number | null;
}

export function inspection(raw: {backends?: unknown; schema?: unknown; maxSeqLen?: unknown} | null): ProgramInspection {
  if (!raw) return {methods: null, prefillChunkTokens: null};
  const bound = forwardTokenBound(raw.schema);
  const maxSeqLen = typeof raw.maxSeqLen === 'number' && Number.isInteger(raw.maxSeqLen) && raw.maxSeqLen > 0 ?
    raw.maxSeqLen : null;
  // RNE clamps the prefiller's max_seq_len to the graph's bound (llm_runner.cpp readPrefillBound).
  const chunk = bound === null ? null : maxSeqLen === null ? bound : Math.min(bound, maxSeqLen);
  return {methods: programMethods(raw.backends), prefillChunkTokens: chunk};
}

/** ExecuTorch llm::Stats fields react-native-executorch returns from generate(). */
export interface GenerationStats {
  numPromptTokens: number;
  numGeneratedTokens: number;
  firstTokenMs: number;
  inferenceStartMs: number;
  inferenceEndMs: number;
}

/** One timed request: the runner's statistics and the worklet thread's steady-clock timestamps. */
export interface TimedRequest {
  stats: GenerationStats;
  start: number;
  /** First token callback; -1 if none came. */
  first: number;
  end: number;
  emitted: number;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/**
 * Metrics of one ExecuTorch request from the runner's own statistics (types.ts ExecutorchStats):
 * prefill = numPromptTokens over firstTokenMs - inferenceStartMs (tokenization, every prefill
 * chunk and the first token's sampling); decode = numGeneratedTokens over inferenceEndMs -
 * firstTokenMs (the decode loop, one forward per token). These are ExecuTorch's own "Prompt
 * evaluation" and "Generated tokens" rates (llm::print_report). TTFT is the worklet thread's
 * steady clock from just before generate() to the first token callback. decodeTokens counts every
 * token the request produced (the first, sampled by the prefill, plus the decode loop's).
 */
export function executorchMetrics(result: TimedRequest, prefillChunkTokens: number | null,
  fixedDecodeTokens: number): SingleRunMetrics {
  const {stats} = result;
  const values = [stats.numPromptTokens, stats.numGeneratedTokens, stats.inferenceStartMs, stats.firstTokenMs,
    stats.inferenceEndMs, result.start, result.first, result.end, result.emitted];
  if (!values.every(finite) || result.first < 0) {
    throw new Error('ExecuTorch returned no token or non-finite statistics');
  }
  const promptMs = stats.firstTokenMs - stats.inferenceStartMs;
  const predictedMs = stats.inferenceEndMs - stats.firstTokenMs;
  if (promptMs <= 0 || predictedMs <= 0 || stats.numPromptTokens < 1 || stats.numGeneratedTokens < 1) {
    throw new Error('ExecuTorch reported a non-positive prefill or decode interval');
  }
  if (result.emitted !== stats.numGeneratedTokens + 1) {
    throw new Error(`ExecuTorch emitted ${result.emitted} tokens for ${stats.numGeneratedTokens} decoded`);
  }
  if (fixedDecodeTokens > 0 && result.emitted !== fixedDecodeTokens) {
    throw new Error(`ExecuTorch decoded ${result.emitted} tokens, not the fixed ${fixedDecodeTokens}`);
  }
  const prefillTps = stats.numPromptTokens * 1000 / promptMs;
  const decodeTps = stats.numGeneratedTokens * 1000 / predictedMs;
  return {
    loadMs: 0, // filled by the protocol runner from load()
    ttftMs: result.first - result.start,
    promptTokens: stats.numPromptTokens,
    prefillTps,
    prefillMethod: 'engine-timings',
    decodeTokens: result.emitted,
    decodeTps,
    decodeMethod: 'engine-timings',
    engineInternal: {promptMs, promptPerSecond: prefillTps, predictedMs, predictedPerSecond: decodeTps},
    executorchStats: {numPromptTokens: stats.numPromptTokens, numGeneratedTokens: stats.numGeneratedTokens,
      inferenceStartMs: stats.inferenceStartMs, firstTokenMs: stats.firstTokenMs, inferenceEndMs: stats.inferenceEndMs,
      emittedTokens: result.emitted, prefillChunkTokens,
      prefillChunks: prefillChunkTokens === null ? null : Math.ceil(stats.numPromptTokens / prefillChunkTokens)},
  };
}
