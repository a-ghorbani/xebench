import { BuildInfo, initLlama, LlamaContext } from 'llama.rn';
import type { BackendId, SingleRunMetrics } from '../types';
import type {
  BenchOnceConfig, EngineAdapter, ExecutionReport, LlamaRnEffectiveSettings, SettingsRequest,
} from './EngineAdapter';
import {monotonicNow} from '../clock';
import {readNativeLibraries} from '../build';
import {backendEvidence, BackendEvidence} from '../backend';
import {llamaCppBuild, NO_RUNTIME_VERSIONS, RuntimeVersions, version} from '../runtime';

import {version as LLAMA_RN_VERSION} from 'llama.rn/package.json';

export interface LlamaRnAdapterOptions {
  /** Absolute path to the GGUF, e.g. /sdcard/Android/data/com.xebenchapp/files/Llama-3.2-1B-Instruct-Q4_K_M.gguf */
  modelPath: string;
  quantLabel: string; // e.g. 'Q4_K_M'
  modelLabel: string; // e.g. 'Llama 3.2 1B'
  nCtx?: number;
  nThreads?: number; // default: llama.rn's own default (perf cores heuristic)
  /** GPU offload layers. >0 => llama.rn's OpenCL backend (librnllama_*_opencl.so). 0 = CPU. */
  nGpuLayers?: number;
  /** Backend devices, e.g. ['HTP0'] for Hexagon NPU (SM8450+). Needs nGpuLayers>0. */
  devices?: string[];
  /** Load and attention settings; each defaults to LLAMA_RN_DEFAULTS. */
  useMmap?: boolean;
  useMlock?: boolean;
  /** false: weight repacking on (llama.cpp extra CPU buffer types). */
  noExtraBufts?: boolean;
  flashAttnType?: 'auto' | 'on' | 'off';
  /**
   * decodeMode fixed: the model's end-of-generation token ids (configuration eogTokenIds). The
   * measured repetitions bias each to -infinity, so decoding runs to n_predict.
   */
  eogTokenIds?: number[];
}

/**
 * Harness defaults for the settings a configuration may set (config.ts LOAD_SETTINGS), as
 * cpu-reference-v1 runs them. Against llama.rn 0.13.0-rc.8 (vendored llama.cpp
 * common/common.h): flash_attn_type auto and no_extra_bufts false equal its defaults. Loading
 * differs: 0.13's default load_mode is AUTO (chosen per device), while use_mmap true /
 * use_mlock false select LLAMA_LOAD_MODE_MMAP, the 0.12.4 behaviour (cpp/jsi/JSIParams.cpp).
 */
export const LLAMA_RN_DEFAULTS = {use_mmap: true, use_mlock: false, no_extra_bufts: false,
  flash_attn_type: 'auto'} as const;

/** Greedy decoding: deterministic and free of sampling-cost skew. */
const COMPLETION = {temperature: 0, seed: 42} as const;

/** The completion result fields that say why llama.rn stopped and how many tokens it decoded. */
export type CompletionStop = {
  stopped_eos?: unknown; stopped_limit?: unknown; stopped_word?: unknown; context_full?: unknown;
  interrupted?: unknown; tokens_predicted?: unknown; timings?: {predicted_n?: unknown};
};

/**
 * Why a fixed-length completion did not produce exactly that many tokens; null when it did.
 * llama.rn produced them exactly when it stopped on the n_predict limit (stopped_limit),
 * not at an end-of-generation token, a stop word, a full context or an interruption, and its decode
 * counter (timings.predicted_n, equal to tokens_predicted) is one less: the first token is sampled
 * from the prompt's logits, and each later one after a one-token decode, which is what the counter
 * and predicted_ms cover (cpp/rn-completion.cpp nextToken, cpp/jsi/JSICompletion.h). So for 128
 * tokens llama.rn reports predicted_n 127, and decode tok/s is 127 tokens over predicted_ms.
 */
export function fixedDecodeError(result: CompletionStop, tokens: number): string | null {
  const truthy = (value: unknown) => value === true || value === 1;
  if (truthy(result.stopped_eos)) return 'stopped at an end-of-generation token';
  if (result.stopped_word) return 'stopped at a stop word';
  if (truthy(result.context_full)) return 'stopped on a full context';
  if (truthy(result.interrupted)) return 'was interrupted';
  if (!truthy(result.stopped_limit)) return 'did not stop on the n_predict limit';
  const decoded = result.timings?.predicted_n;
  if (decoded !== tokens - 1 || (result.tokens_predicted !== undefined && result.tokens_predicted !== decoded)) {
    return `decoded ${String(decoded)} tokens after the first, not ${tokens - 1}`;
  }
  return null;
}

/** Printable ASCII of at most max characters; anything else stays unknown (null). */
function bounded(value: unknown, max: number): string | null {
  return typeof value === 'string' && value.length <= max && /^[\x20-\x7e]*$/.test(value) ? value : null;
}

/** Bounded copy of the context fields llama.rn reports after initLlama(), plus the mapped native libraries. */
export function executionReport(ctx: Partial<Record<Exclude<keyof ExecutionReport, 'nativeLibraries'>, unknown>>,
  nativeLibraries: ExecutionReport['nativeLibraries'] = null): ExecutionReport {
  const devices = Array.isArray(ctx.devices) && ctx.devices.length <= 16 &&
    ctx.devices.every(device => bounded(device, 64) !== null && device !== '') ? [...ctx.devices] as string[] : null;
  const lib = bounded(ctx.androidLib, 64);
  return {
    gpu: typeof ctx.gpu === 'boolean' ? ctx.gpu : null,
    reasonNoGPU: bounded(ctx.reasonNoGPU, 256),
    devices,
    androidLib: lib !== null && /^[a-z0-9_]+$/.test(lib) ? lib : null,
    systemInfo: bounded(ctx.systemInfo, 2048),
    nativeLibraries,
  };
}

export class LlamaRnAdapter implements EngineAdapter {
  readonly engine = 'llama.cpp' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: LlamaRnAdapterOptions;
  private ctx: LlamaContext | null = null;
  private lastReport: ExecutionReport | null = null;
  /** Passed verbatim to initLlama (with the model path) and recorded as effective settings. */
  private readonly init: LlamaRnEffectiveSettings['init'];
  /** decodeMode fixed: the decode length of every later benchOnce (0 = until end of generation). */
  private fixedDecodeTokens = 0;

  constructor(opts: LlamaRnAdapterOptions) {
    this.opts = opts;
    const usesHtp = (opts.devices ?? []).some(d => d.toUpperCase().startsWith('HTP'));
    this.backend = usesHtp ? 'npu' : (opts.nGpuLayers ?? 0) > 0 ? 'gpu' : 'cpu';
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
    // Every setting is passed explicitly, so the record states them. Against llama.rn
    // 0.13.0-rc.8 (vendored llama.cpp common.h): n_ubatch 512 and f16 K/V caches equal its
    // defaults; n_batch 512 is the harness's choice (llama.cpp's default is 2048). Load and
    // attention settings come from the configured run, else LLAMA_RN_DEFAULTS. use_mmap false
    // with use_mlock false selects LLAMA_LOAD_MODE_NONE: the weights are read into memory.
    // state_cache_budget_mb 0 turns off llama.rn's prompt state cache (cpp/rn-completion.cpp
    // probeStateCache): every repetition is a fresh single-turn context, so the cache has nothing
    // to reuse, and with it on a sliding-window model's first prompt is split after the BOS token
    // (a one-token batch that llama.cpp's prompt counters do not count).
    this.init = {
      n_ctx: opts.nCtx ?? 2048,
      n_batch: 512,
      n_ubatch: 512,
      n_threads: opts.nThreads ?? null, // null: llama.rn's own default (perf cores heuristic)
      n_gpu_layers: opts.nGpuLayers ?? 0, // >0 => GPU/NPU offload
      use_mmap: opts.useMmap ?? LLAMA_RN_DEFAULTS.use_mmap,
      use_mlock: opts.useMlock ?? LLAMA_RN_DEFAULTS.use_mlock,
      no_extra_bufts: opts.noExtraBufts ?? LLAMA_RN_DEFAULTS.no_extra_bufts,
      flash_attn_type: opts.flashAttnType ?? LLAMA_RN_DEFAULTS.flash_attn_type,
      cache_type_k: 'f16',
      cache_type_v: 'f16',
      ...(opts.devices ? {devices: [...opts.devices]} : {}), // ['HTP0'] => Hexagon NPU
      state_cache_budget_mb: 0,
    };
  }

  engineVersion(): string {
    const be = this.backend === 'gpu' ? ' (OpenCL)' : this.backend === 'npu' ? ` (Hexagon ${this.opts.devices?.join(',')})` : '';
    return `llama.rn ${LLAMA_RN_VERSION}${be}`;
  }

  /** llama.cpp as vendored by this llama.rn (its exported BuildInfo) and llama.rn itself. */
  runtimeVersions(): RuntimeVersions {
    return {...NO_RUNTIME_VERSIONS, llamaCpp: llamaCppBuild(BuildInfo), llamaRn: version(LLAMA_RN_VERSION)};
  }

  settings() {
    return {
      nThreads: this.opts.nThreads ?? null,
      nCtx: this.opts.nCtx ?? 2048,
    };
  }

  /**
   * The parameters of a request. decodeMode fixed (the measured repetitions) adds logit_bias:
   * [id, false] for every end-of-generation token, llama.rn's spelling of a -infinity bias
   * (cpp/jsi/JSIParams.cpp). It is not ignore_eos, which biases the vocabulary's EOS token alone
   * while the completion loop stops at any end-of-generation token (llama_vocab_is_eog).
   */
  effectiveSettings(request: SettingsRequest): LlamaRnEffectiveSettings {
    const eog = this.opts.eogTokenIds ?? [];
    if (request.decodeMode === 'fixed' && !eog.length) {
      throw new Error('llama.rn fixed decoding needs the model\'s end-of-generation token ids');
    }
    return {binding: 'llama.rn', init: {...this.init, ...(this.init.devices ? {devices: [...this.init.devices]} : {})},
      completion: {n_predict: request.maxDecodeTokens, ...COMPLETION,
        ...(request.decodeMode === 'fixed' ? {logit_bias: eog.map(id => [id, false] as [number, false])} : {})},
      unlisted: 'binding-defaults',
      distribution: {source: 'npm', package: 'llama.rn', version: LLAMA_RN_VERSION, patches: []}};
  }

  /**
   * The completion parameters of a request, as passed to llama.rn. The bias entries are llama.rn's
   * [id, false] form, which its TypeScript type (number pairs) does not spell.
   */
  private completion(request: SettingsRequest) {
    const {logit_bias: bias, ...completion} = this.effectiveSettings(request).completion;
    return {...completion, ...(bias ? {logit_bias: bias as unknown as number[][]} : {})};
  }

  /** decodeMode fixed: every later benchOnce decodes exactly decodeTokens tokens (the gate is not affected). */
  prepareMeasurement(options: {decodeMode: 'fixed'; decodeTokens: number}) {
    if (options.decodeMode !== 'fixed' || !Number.isInteger(options.decodeTokens) || options.decodeTokens < 2) {
      throw new Error('llama.rn fixed decoding needs a decode length of at least 2');
    }
    if (!this.opts.eogTokenIds?.length) {
      throw new Error('llama.rn fixed decoding needs the model\'s end-of-generation token ids');
    }
    this.fixedDecodeTokens = options.decodeTokens;
  }

  executionReport(): ExecutionReport {
    if (!this.lastReport) throw new Error('llama.rn: load() first');
    return this.lastReport;
  }

  async load(): Promise<number> {
    this.lastReport = null;
    const {n_threads: nThreads, ...init} = this.init;
    const t0 = monotonicNow();
    this.ctx = await initLlama({
      model: this.opts.modelPath,
      ...init,
      ...(nThreads === null ? {} : {n_threads: nThreads}),
    });
    const loadMs = monotonicNow() - t0;
    // Compile-time guard: if a llama.rn upgrade renames or drops these context fields,
    // tsc fails here instead of every report silently becoming null.
    const reported: Pick<LlamaContext, 'gpu' | 'reasonNoGPU' | 'devices' | 'androidLib' | 'systemInfo'> = this.ctx;
    this.lastReport = executionReport(reported, await readNativeLibraries());
    return loadMs;
  }

  /** accelerator-v1 backend evidence, derived from the same execution report (backend.ts rules). */
  backendEvidence(): BackendEvidence {
    const report = this.lastReport && {gpu: this.lastReport.gpu, devices: this.lastReport.devices,
      androidLib: this.lastReport.androidLib, reasonNoGpu: this.lastReport.reasonNoGPU};
    return backendEvidence(this.engine, this.backend, report ? 'engine-report' : 'none', report, null);
  }

  /** Greedy reply text for the correctness gate (the same completion settings as benchOnce). */
  async generate(cfg: BenchOnceConfig): Promise<string> {
    if (!this.ctx) throw new Error('llama.rn: load() first');
    const result = await this.ctx.completion({prompt: cfg.prompt, ...this.completion({maxDecodeTokens: cfg.maxDecodeTokens})});
    return result.text;
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    if (!this.ctx) throw new Error('llama.rn: load() first');
    const fixed = this.fixedDecodeTokens > 0;
    if (fixed && cfg.maxDecodeTokens !== this.fixedDecodeTokens) {
      throw new Error(`llama.rn: decode length ${cfg.maxDecodeTokens} is not the fixed ${this.fixedDecodeTokens}`);
    }
    const completion = this.completion({maxDecodeTokens: cfg.maxDecodeTokens, ...(fixed ? {decodeMode: 'fixed'} : {})});
    const t0 = monotonicNow();
    let tFirstToken: number | null = null;

    const result = await this.ctx.completion(
      {
        prompt: cfg.prompt, // raw, pre-templated — no chat wrapper
        ...completion,
      },
      () => {
        if (tFirstToken === null) tFirstToken = monotonicNow();
      },
    );

    const t = result.timings;
    if (tFirstToken === null) {
      throw new Error('llama.rn: no token callback; TTFT is unavailable');
    }
    const ttftMs = tFirstToken - t0;
    if (fixed) {
      const error = fixedDecodeError(result, this.fixedDecodeTokens);
      if (error) {
        throw new Error(`llama.rn ${error}: not the fixed ${this.fixedDecodeTokens}-token decode`);
      }
    }
    return {
      loadMs: 0, // filled by protocol runner
      ttftMs,
      promptTokens: t.prompt_n,
      // Engine-internal counters — the gold standard for llama.cpp
      prefillTps: t.prompt_per_second,
      prefillMethod: 'engine-timings',
      // Fixed decoding: every token produced (verified above); otherwise llama.rn's decode counter,
      // which leaves out the first token (sampled from the prompt's logits).
      decodeTokens: fixed ? this.fixedDecodeTokens : t.predicted_n,
      decodeTps: t.predicted_per_second,
      decodeMethod: 'engine-timings',
      engineInternal: {
        promptMs: t.prompt_ms,
        promptPerSecond: t.prompt_per_second,
        predictedMs: t.predicted_ms,
        predictedPerSecond: t.predicted_per_second,
      },
    };
  }

  async release(): Promise<void> {
    if (this.ctx) {
      await this.ctx.release();
      this.ctx = null;
    }
  }
}
