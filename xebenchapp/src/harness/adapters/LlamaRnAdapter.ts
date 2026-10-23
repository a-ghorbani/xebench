import { BuildInfo, initLlama, LlamaContext } from 'llama.rn';
import type { BackendId, SingleRunMetrics } from '../types';
import type {
  BenchOnceConfig, EngineAdapter, ExecutionReport, LlamaRnEffectiveSettings,
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

  effectiveSettings(request: Pick<BenchOnceConfig, 'maxDecodeTokens'>): LlamaRnEffectiveSettings {
    return {binding: 'llama.rn', init: {...this.init, ...(this.init.devices ? {devices: [...this.init.devices]} : {})},
      completion: {n_predict: request.maxDecodeTokens, ...COMPLETION}, unlisted: 'binding-defaults',
      distribution: {source: 'npm', package: 'llama.rn', version: LLAMA_RN_VERSION, patches: []}};
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
    const result = await this.ctx.completion({prompt: cfg.prompt, ...this.effectiveSettings(cfg).completion});
    return result.text;
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    if (!this.ctx) throw new Error('llama.rn: load() first');
    const t0 = monotonicNow();
    let tFirstToken: number | null = null;

    const result = await this.ctx.completion(
      {
        prompt: cfg.prompt, // raw, pre-templated — no chat wrapper
        ...this.effectiveSettings(cfg).completion,
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
    return {
      loadMs: 0, // filled by protocol runner
      ttftMs,
      promptTokens: t.prompt_n,
      // Engine-internal counters — the gold standard for llama.cpp
      prefillTps: t.prompt_per_second,
      prefillMethod: 'engine-timings',
      decodeTokens: t.predicted_n,
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
