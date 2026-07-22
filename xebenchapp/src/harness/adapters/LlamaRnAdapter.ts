import { initLlama, LlamaContext } from 'llama.rn';
import type { BackendId, SingleRunMetrics } from '../types';
import type { BenchOnceConfig, EngineAdapter } from './EngineAdapter';

const LLAMA_RN_VERSION = '0.12.4'; // keep in sync with package.json

export interface LlamaRnAdapterOptions {
  /** Absolute path to the GGUF, e.g. /data/local/tmp/xebench/Llama-3.2-1B-Instruct-Q4_K_M.gguf */
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

export class LlamaRnAdapter implements EngineAdapter {
  readonly engine = 'llama.cpp' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: LlamaRnAdapterOptions;
  private ctx: LlamaContext | null = null;

  constructor(opts: LlamaRnAdapterOptions) {
    this.opts = opts;
    const usesHtp = (opts.devices ?? []).some(d => d.toUpperCase().startsWith('HTP'));
    this.backend = usesHtp ? 'npu' : (opts.nGpuLayers ?? 0) > 0 ? 'gpu' : 'cpu';
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
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

  async load(): Promise<number> {
    const t0 = Date.now();
    this.ctx = await initLlama({
      model: this.opts.modelPath,
      n_ctx: this.opts.nCtx ?? 2048,
      n_batch: 512,
      n_gpu_layers: this.opts.nGpuLayers ?? 0, // >0 => GPU/NPU offload
      ...(this.opts.devices ? { devices: this.opts.devices } : {}), // ['HTP0'] => Hexagon NPU
      ...(this.opts.nThreads ? { n_threads: this.opts.nThreads } : {}),
    });
    return Date.now() - t0;
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    if (!this.ctx) throw new Error('llama.rn: load() first');
    const t0 = Date.now();
    let tFirstToken = 0;

    const result = await this.ctx.completion(
      {
        prompt: cfg.prompt, // raw, pre-templated — no chat wrapper
        n_predict: cfg.maxDecodeTokens,
        temperature: 0, // greedy: deterministic + no sampling-cost skew
        seed: 42,
      },
      () => {
        if (tFirstToken === 0) tFirstToken = Date.now();
      },
    );

    const t = result.timings;
    const ttftMs = (tFirstToken || Date.now()) - t0;
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
