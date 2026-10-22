import {NativeModules} from 'react-native';
import type {BackendId, SingleRunMetrics} from '../types';
import type {BenchOnceConfig, EngineAdapter} from './EngineAdapter';
import {backendEvidence, BackendEvidence} from '../backend';
import {NO_RUNTIME_VERSIONS, RuntimeVersions, version} from '../runtime';

/**
 * GENIE-X (Qualcomm geniex-android) adapter, driven in the app through GenieXBenchModule.kt (geniex
 * flavor). The NPU runs a QAIRT bundle (a directory: genie_config.json and the QNN context binaries)
 * through GENIE-X's `qairt` plugin; the CPU runs a GGUF through its `llama_cpp` plugin and serves only
 * as the output gate's reference.
 *
 * load() creates a fresh model instance (LlmCreateInput), release() destroys it. Every request sends
 * the pre-templated prompt as GENIE-X's raw prompt (Llm.generate: the plugin tokenizes the string as
 * given and applies no chat template), greedy (temperature below zero is GENIE-X's argmax sentinel,
 * top-k 1), and decodes at most maxDecodeTokens. GENIE-X 0.8.0 always stops at the end of turn: its
 * GenerationConfig has no option to ignore it (geniex-qairt LLMModel::generate breaks on an EOS/EOG
 * token; the llama_cpp plugin likewise), so decodeTokens is what the model produced up to the limit.
 *
 * Metrics (genieXMetrics):
 *  - ttftMs: request -> first non-empty token callback, timed natively on the worker thread
 *    (as LiteRT-LM's), so it includes tokenization and the JNI callback.
 *  - promptTokens: the engine's prompt token count (ProfilingData.promptTokens).
 *  - prefillTps: promptTokens / the engine's prompt time. On the NPU, GENIE-X's QAIRT plugin reports
 *    its own TTFT as the prompt time (native clock from after tokenization to the first sampled
 *    token: prefill plus the first sampling step), so the method is 'ttft-derived'. On the CPU, the
 *    llama_cpp plugin times the prefill batches alone: 'engine-timings'.
 *  - decodeTokens / decodeTps: tokens produced, over the engine's decode time (the window from the
 *    first produced token to the end of generation, which holds one forward pass per produced token).
 *    The QAIRT plugin counts the end-of-turn token in generatedTokens when generation ended there
 *    (Genie's convention); it is not a produced token, so it is subtracted. 'engine-timings'.
 *  - engineInternal: GENIE-X's own prompt and decode times (ProfilingData.promptTimeMs and
 *    decodeTimeMs) with the speeds above, which are the engine counters over those times. GENIE-X's
 *    own decodingSpeed differs only by the end-of-turn token its QAIRT plugin counts.
 */
export interface GenieXProfile {
  ttftMs: number;
  promptTimeMs: number;
  decodeTimeMs: number;
  promptTokens: number;
  generatedTokens: number;
  prefillSpeed: number;
  decodingSpeed: number;
  stopReason: string;
}

/** Versions GenieXBenchModule reports from the APK: geniex-android as Gradle resolved it, and the
 * QAIRT build string in the packaged libQnnHtp.so, which the QAIRT plugin loads. QAIRT takes part in
 * NPU runs only. */
export function genieXRuntimeVersions(constants: unknown, backend: BackendId): RuntimeVersions {
  const value = (constants ?? {}) as Record<string, unknown>;
  return {...NO_RUNTIME_VERSIONS, genie: version(value.geniexVersion),
    qairt: backend === 'npu' ? version(value.qairtVersion) : null};
}

/** Exported label alphabet: no commas (scripts/export_evidence.py LABEL); mirrored by raw_session.py. */
export const genieXEngineVersion = (backend: BackendId, versions: RuntimeVersions) => {
  const geniex = `geniex-android ${versions.genie ?? 'unknown'}`;
  return backend === 'npu' ? `GENIE-X ${geniex} + QAIRT ${versions.qairt ?? 'unknown'} (npu)` :
    `GENIE-X ${geniex} (${backend})`;
};

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const count = (value: unknown): value is number => finite(value) && Number.isInteger(value) && value >= 0;
const STOP_REASONS = ['eos', 'length'];

/** One measured request's metrics from GENIE-X's ProfilingData (see the class comment). */
export function genieXMetrics(backend: BackendId, ttftMs: unknown, profile: GenieXProfile | null | undefined,
  maxDecodeTokens: number): SingleRunMetrics {
  if (!profile) {
    throw new Error('GENIE-X returned no profiling data');
  }
  const {promptTimeMs, decodeTimeMs, prefillSpeed, decodingSpeed, promptTokens, generatedTokens} = profile;
  if (![ttftMs, profile.ttftMs, promptTimeMs, decodeTimeMs, prefillSpeed, decodingSpeed].every(finite) ||
      !count(promptTokens) || !count(generatedTokens)) {
    throw new Error('GENIE-X returned non-finite timings or counts');
  }
  if (!STOP_REASONS.includes(profile.stopReason)) {
    throw new Error(`GENIE-X stopped for ${String(profile.stopReason)}, not at the end of turn or the decode limit`);
  }
  if (backend === 'npu' && promptTimeMs !== profile.ttftMs) {
    // The QAIRT plugin (0.8.0) reports its TTFT as the prompt time; anything else needs a new definition.
    throw new Error('GENIE-X QAIRT prompt time is not its TTFT; the prefill definition no longer holds');
  }
  const decodeTokens = generatedTokens - (backend === 'npu' && profile.stopReason === 'eos' ? 1 : 0);
  if (promptTokens < 1 || promptTimeMs <= 0 || decodeTokens < 1 || decodeTimeMs <= 0) {
    throw new Error('GENIE-X measured no prompt or no decoded token');
  }
  // geniex-android 0.8.0's JNI computes its speeds as these counts over these times; a different
  // relation means the SDK changed what it reports.
  const near = (a: number, b: number) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(a), Math.abs(b));
  if (!near(prefillSpeed, promptTokens / (promptTimeMs / 1000)) ||
      !near(decodingSpeed, generatedTokens / (decodeTimeMs / 1000))) {
    throw new Error('GENIE-X speeds are not its token counts over its times; the metric definitions no longer hold');
  }
  if (decodeTokens > maxDecodeTokens) {
    throw new Error(`GENIE-X decoded ${decodeTokens} tokens, more than the limit ${maxDecodeTokens}`);
  }
  const prefillTps = promptTokens / (promptTimeMs / 1000);
  const decodeTps = decodeTokens / (decodeTimeMs / 1000);
  return {
    loadMs: 0, // filled by the protocol runner from load()
    ttftMs: ttftMs as number,
    promptTokens,
    prefillTps,
    prefillMethod: backend === 'npu' ? 'ttft-derived' : 'engine-timings',
    decodeTokens,
    decodeTps,
    decodeMethod: 'engine-timings',
    engineInternal: {promptMs: promptTimeMs, promptPerSecond: prefillTps, predictedMs: decodeTimeMs,
      predictedPerSecond: decodeTps},
  };
}

export interface GenieXAdapterOptions {
  /** CPU: the GGUF path. NPU: the QAIRT bundle directory (holding genie_config.json). */
  modelPath: string;
  backend: 'cpu' | 'npu';
  quantLabel: string;
  modelLabel: string;
  /** CPU only (llama_cpp plugin); null on the NPU, where the bundle fixes them. */
  nThreads: number | null;
  nCtx: number | null;
}

type Libraries = {before: string[] | null; after: string[] | null};

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every(item => typeof item === 'string') ? value as string[] : null;

export class GenieXAdapter implements EngineAdapter {
  readonly engine = 'genie-x' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: GenieXAdapterOptions;
  private libraries: Libraries | null = null;
  private native: any;

  constructor(opts: GenieXAdapterOptions) {
    this.native = NativeModules.GenieXBench;
    if (!this.native) {
      throw new Error('GenieXBench native module not linked — build the geniex flavor (assembleGeniexRelease)');
    }
    if ((opts.backend === 'cpu') !== (opts.nThreads !== null && opts.nCtx !== null)) {
      throw new Error('GENIE-X CPU runs need nThreads and nCtx; NPU runs take neither');
    }
    this.opts = opts;
    this.backend = opts.backend;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  engineVersion(): string {
    return genieXEngineVersion(this.opts.backend, this.runtimeVersions());
  }

  runtimeVersions(): RuntimeVersions {
    const constants = typeof this.native.getConstants === 'function' ? this.native.getConstants() : this.native;
    return genieXRuntimeVersions(constants, this.opts.backend);
  }

  settings() {
    return {nThreads: this.opts.nThreads, nCtx: this.opts.nCtx};
  }

  /** The path GENIE-X's create takes: the GGUF, or genie_config.json inside the bundle directory. */
  private createPath(): string {
    return this.opts.backend === 'npu' ? `${this.opts.modelPath}/genie_config.json` : this.opts.modelPath;
  }

  async load(): Promise<number> {
    this.libraries = null;
    const result = await this.native.load(this.createPath(), this.opts.backend, this.opts.nThreads ?? 0,
      this.opts.nCtx ?? 0);
    this.libraries = {before: strings(result?.librariesBefore), after: strings(result?.librariesAfter)};
    if (!finite(result?.loadMs)) {
      throw new Error('GENIE-X load returned no duration');
    }
    return result.loadMs;
  }

  backendEvidence(): BackendEvidence {
    return backendEvidence(this.engine, this.backend, this.libraries ? 'process-libraries' : 'none', null,
      this.libraries);
  }

  /** Greedy reply text for the output gate; each request starts from an empty context. */
  async generate(cfg: BenchOnceConfig): Promise<string> {
    const result = await this.native.generate(cfg.prompt, cfg.maxDecodeTokens);
    if (typeof result?.text !== 'string') {
      throw new Error('GENIE-X returned no text');
    }
    return result.text;
  }

  /** cfg.prompt is sent verbatim as the raw prompt (cfg.text, a plain user message, is not used). */
  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    const result = await this.native.generate(cfg.prompt, cfg.maxDecodeTokens);
    return genieXMetrics(this.backend, result?.ttftMs, result?.profile, cfg.maxDecodeTokens);
  }

  async release(): Promise<void> {
    this.libraries = null;
    await this.native.release();
  }
}
