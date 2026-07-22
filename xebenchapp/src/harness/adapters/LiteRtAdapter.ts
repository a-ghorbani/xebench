import {NativeModules} from 'react-native';
import type {BackendId, SingleRunMetrics} from '../types';
import type {BenchOnceConfig, EngineAdapter} from './EngineAdapter';

const LITERTLM_VERSION = 'litertlm-android 0.14.0';

const {LiteRtBench} = NativeModules;

export interface LiteRtAdapterOptions {
  /** Absolute path to the .litertlm on device */
  modelPath: string;
  /** 'cpu' | 'gpu' | 'npu' */
  backend: 'cpu' | 'gpu' | 'npu';
  quantLabel: string; // e.g. 'int4'
  modelLabel: string; // e.g. 'Gemma 3 1B'
}

/**
 * LiteRT-LM adapter. Unlike ExecuTorch (callback-derived), LiteRT-LM's native
 * benchmark() reports prefill AND decode tok/s from its own counters — so both
 * are 'engine-timings' (gold standard), directly comparable to llama.cpp.
 *
 * The native call does load + prefill + decode + release in one shot, so the
 * EngineAdapter load()/release() are no-ops and each benchOnce() is a full cold
 * cycle (fresh engine init internally).
 */
export class LiteRtAdapter implements EngineAdapter {
  readonly engine = 'litert-lm' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: LiteRtAdapterOptions;

  constructor(opts: LiteRtAdapterOptions) {
    if (!LiteRtBench) {
      throw new Error('LiteRtBench native module not linked — check LiteRtBenchPackage registration');
    }
    this.opts = opts;
    this.backend = opts.backend;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  engineVersion(): string {
    return `LiteRT-LM (${LITERTLM_VERSION}, ${this.opts.backend})`;
  }

  settings() {
    return {nThreads: null, nCtx: null};
  }

  async load(): Promise<number> {
    return 0; // native bench() loads internally
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    // benchmark(prefillTokens=512, decodeTokens=cfg.maxDecodeTokens) — pp512/tg128
    // convention, matching the llama-bench reference. Prompt tokens are synthetic
    // (same as llama-bench), so this is a pp/tg throughput measurement, not a
    // real-prompt TTFT run.
    const r = await LiteRtBench.bench(this.opts.modelPath, this.opts.backend, 512, cfg.maxDecodeTokens);
    return {
      loadMs: r.loadMs ?? 0,
      ttftMs: Math.round(r.ttftMs ?? 0),
      promptTokens: r.promptTokens ?? 0,
      prefillTps: r.prefillTps ?? 0,
      prefillMethod: 'engine-timings',
      decodeTokens: r.decodeTokens ?? 0,
      decodeTps: r.decodeTps ?? 0,
      decodeMethod: 'engine-timings',
    };
  }

  async release(): Promise<void> {
    // native bench() releases internally
  }
}
