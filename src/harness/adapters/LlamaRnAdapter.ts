import { initLlama, LlamaContext } from 'llama.rn';
import type { SingleRunMetrics } from '../types';
import type { BenchOnceConfig, EngineAdapter } from './EngineAdapter';

const LLAMA_RN_VERSION = '0.12.5'; // keep in sync with package.json

export interface LlamaRnAdapterOptions {
  /** Absolute path to the GGUF, e.g. /data/local/tmp/xebench/Llama-3.2-1B-Instruct-Q4_K_M.gguf */
  modelPath: string;
  quantLabel: string; // e.g. 'Q4_K_M'
  modelLabel: string; // e.g. 'Llama 3.2 1B'
  nCtx?: number;
  nThreads?: number; // default: llama.rn's own default (perf cores heuristic)
}

export class LlamaRnAdapter implements EngineAdapter {
  readonly engine = 'llama.cpp' as const;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: LlamaRnAdapterOptions;
  private ctx: LlamaContext | null = null;

  constructor(opts: LlamaRnAdapterOptions) {
    this.opts = opts;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  engineVersion(): string {
    return `llama.rn ${LLAMA_RN_VERSION}`;
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
      n_gpu_layers: 0, // CPU backend for v1 (Android GPU/OpenCL out of scope)
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
