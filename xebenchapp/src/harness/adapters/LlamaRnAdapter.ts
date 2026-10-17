import { initLlama, LlamaContext } from 'llama.rn';
import type { BackendId, SingleRunMetrics } from '../types';
import type {
  BenchOnceConfig, EngineAdapter, ExecutionReport, LlamaRnEffectiveSettings,
} from './EngineAdapter';
import {monotonicNow} from '../clock';

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
}

/** Greedy decoding: deterministic and free of sampling-cost skew. */
const COMPLETION = {temperature: 0, seed: 42} as const;

/** Printable ASCII of at most max characters; anything else stays unknown (null). */
function bounded(value: unknown, max: number): string | null {
  return typeof value === 'string' && value.length <= max && /^[\x20-\x7e]*$/.test(value) ? value : null;
}

/** Bounded copy of the context fields llama.rn 0.12 reports after initLlama(). */
export function executionReport(ctx: Partial<Record<keyof ExecutionReport, unknown>>): ExecutionReport {
  const devices = Array.isArray(ctx.devices) && ctx.devices.length <= 16 &&
    ctx.devices.every(device => bounded(device, 64) !== null && device !== '') ? [...ctx.devices] as string[] : null;
  const lib = bounded(ctx.androidLib, 64);
  return {
    gpu: typeof ctx.gpu === 'boolean' ? ctx.gpu : null,
    reasonNoGPU: bounded(ctx.reasonNoGPU, 256),
    devices,
    androidLib: lib !== null && /^[a-z0-9_]+$/.test(lib) ? lib : null,
    systemInfo: bounded(ctx.systemInfo, 2048),
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
    // Every setting is passed explicitly, including those equal to llama.rn 0.12.4 defaults
    // (n_ubatch, use_mmap, use_mlock, flash_attn_type, cache types), so the record states them.
    // n_batch 512 is the harness's choice (llama.cpp's default is 2048).
    this.init = {
      n_ctx: opts.nCtx ?? 2048,
      n_batch: 512,
      n_ubatch: 512,
      n_threads: opts.nThreads ?? null, // null: llama.rn's own default (perf cores heuristic)
      n_gpu_layers: opts.nGpuLayers ?? 0, // >0 => GPU/NPU offload
      use_mmap: true,
      use_mlock: false,
      flash_attn_type: 'auto',
      cache_type_k: 'f16',
      cache_type_v: 'f16',
      ...(opts.devices ? {devices: [...opts.devices]} : {}), // ['HTP0'] => Hexagon NPU
    };
  }

  engineVersion(): string {
    const be = this.backend === 'gpu' ? ' (OpenCL)' : this.backend === 'npu' ? ` (Hexagon ${this.opts.devices?.join(',')})` : '';
    return `llama.rn ${LLAMA_RN_VERSION}${be}`;
  }

  settings() {
    return {
      nThreads: this.opts.nThreads ?? null,
      nCtx: this.opts.nCtx ?? 2048,
    };
  }

  effectiveSettings(request: Pick<BenchOnceConfig, 'maxDecodeTokens'>): LlamaRnEffectiveSettings {
    return {binding: 'llama.rn', init: {...this.init, ...(this.init.devices ? {devices: [...this.init.devices]} : {})},
      completion: {n_predict: request.maxDecodeTokens, ...COMPLETION}, unlisted: 'binding-defaults'};
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
    this.lastReport = executionReport(reported);
    return loadMs;
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
