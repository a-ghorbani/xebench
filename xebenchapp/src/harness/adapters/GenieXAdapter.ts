import {NativeModules} from 'react-native';
import type {BackendId, SingleRunMetrics} from '../types';
import type {BenchOnceConfig, EngineAdapter} from './EngineAdapter';

/**
 * GENIE-X (Qualcomm) adapter — SCAFFOLD, NOT YET WIRED INTO THE BUILD.
 *
 * Status (2026-07-18): implemented but not integrated/tested. Reasons:
 *  - GENIE-X runs ONLY on Snapdragon 8 Elite / 8 Elite Gen 5. The only such
 *    device in the lab is the POCO F7 Ultra (myron, SM8850), which is currently
 *    install-blocked by HyperOS ("Install via USB" toggle, user-gated).
 *  - Its distinct value is the QAIRT/Hexagon NPU path; its GGUF path uses the
 *    `llama_cpp` plugin (== our llama.cpp engine, redundant to bench).
 *  - The NPU path needs a precompiled QAIRT bundle from Qualcomm AI Hub, not a GGUF.
 *
 * To activate: add `implementation("com.qualcomm.qti:geniex-android:0.3.1")`,
 * write GenieXBenchModule.kt (design in GENIEX-INTEGRATION.md), register it, and
 * add configs to App RUNS. See GENIEX-INTEGRATION.md for the full wiring guide.
 *
 * Measurement note: GENIE-X streams tokens via generateStreamFlow (callbacks),
 * so like ExecuTorch its prefill/decode are ttft-/callback-derived unless
 * LlmGenerateResult exposes engine counters (TBD on real hardware).
 */
const GENIEX_VERSION = 'geniex-android 0.3.1';

const {GenieXBench} = NativeModules;

export interface GenieXAdapterOptions {
  /** GGUF (llama_cpp plugin) or QAIRT bundle dir (qairt plugin) */
  modelPath: string;
  /** 'cpu' | 'gpu' (Adreno) | 'npu' (Hexagon/QAIRT) */
  backend: 'cpu' | 'gpu' | 'npu';
  quantLabel: string;
  modelLabel: string;
}

export class GenieXAdapter implements EngineAdapter {
  readonly engine = 'genie-x' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: GenieXAdapterOptions;

  constructor(opts: GenieXAdapterOptions) {
    if (!GenieXBench) {
      throw new Error(
        'GenieXBench native module not linked — GENIE-X is scaffold-only. See GENIEX-INTEGRATION.md',
      );
    }
    this.opts = opts;
    this.backend = opts.backend;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  engineVersion(): string {
    return `GENIE-X (${GENIEX_VERSION}, ${this.opts.backend})`;
  }

  settings() {
    return {nThreads: null, nCtx: null};
  }

  async load(): Promise<number> {
    return 0; // native bench() builds the LlmWrapper internally
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    const r = await GenieXBench.bench(this.opts.modelPath, this.opts.backend, cfg.prompt, cfg.maxDecodeTokens);
    return {
      loadMs: r.loadMs ?? 0,
      ttftMs: Math.round(r.ttftMs ?? 0),
      promptTokens: r.promptTokens ?? 0,
      prefillTps: r.prefillTps ?? 0,
      prefillMethod: r.engineCounters ? 'engine-timings' : 'ttft-derived',
      decodeTokens: r.decodeTokens ?? 0,
      decodeTps: r.decodeTps ?? 0,
      decodeMethod: r.engineCounters ? 'engine-timings' : 'callback-derived',
    };
  }

  async release(): Promise<void> {}
}
