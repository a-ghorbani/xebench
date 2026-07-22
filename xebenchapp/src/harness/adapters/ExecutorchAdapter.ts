import { LLMModule } from 'react-native-executorch';
import type { SingleRunMetrics } from '../types';
import type { BenchOnceConfig, EngineAdapter } from './EngineAdapter';

const RN_EXECUTORCH_VERSION = '0.9.2'; // keep in sync with package.json

export interface ExecutorchAdapterOptions {
  /** file:// path or HF URL for the .pte (SpinQuant XNNPACK export) */
  modelSource: string;
  /** tokenizer.json (HF tokenizers format) */
  tokenizerSource: string;
  /** tokenizer_config.json */
  tokenizerConfigSource: string;
  quantLabel: string; // e.g. 'SpinQuant 4-bit'
  modelLabel: string; // e.g. 'Llama 3.2 1B'
}

/**
 * react-native-executorch v0.10 adapter (XNNPACK backend).
 *
 * Measurement notes (methodology-relevant):
 * - forward() takes the raw pre-templated string -> same input bytes as llama.rn.
 * - No maxTokens knob exists; we count tokens in the callback and interrupt()
 *   at the budget. The runner may emit one extra token after interrupt — we
 *   compute decode t/s from callback timestamps over the counted window, so
 *   the overshoot does not skew the number.
 * - generationConfig outputTokenBatchSize=1, batchTimeInterval=0 so every
 *   token reaches JS individually -> honest TTFT and per-token timestamps.
 * - No engine-internal timing counters are exposed; prefill is TTFT-derived
 *   (promptTokens / (firstToken - start)) and flagged as such. This includes
 *   one decode step + JS callback hop; with a ~512-token prompt the bias is
 *   small and it biases AGAINST the engine (conservative).
 */
export class ExecutorchAdapter implements EngineAdapter {
  readonly engine = 'executorch' as const;
  readonly backend = 'cpu' as const; // XNNPACK
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: ExecutorchAdapterOptions;
  private llm: LLMModule | null = null;

  // per-run token timing state, written by the module-level token callback
  private tokenTimestamps: number[] = [];
  private maxDecodeTokens = Infinity;

  constructor(opts: ExecutorchAdapterOptions) {
    this.opts = opts;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelSource.split('/').pop() ?? opts.modelSource;
  }

  engineVersion(): string {
    return `react-native-executorch ${RN_EXECUTORCH_VERSION} (XNNPACK)`;
  }

  settings() {
    // Context length is baked into the .pte at export time (2048 for the
    // software-mansion Llama 3.2 exports); thread count is runtime-internal.
    return { nThreads: null, nCtx: 2048 };
  }

  // Batch tokens per callback so the JS bridge doesn't block the native decode
  // loop per-token. Decode is measured from total forward() wall-time minus TTFT.
  private static readonly BATCH = 16;
  private batchCount = 0;

  async load(): Promise<number> {
    const t0 = Date.now();
    this.llm = await LLMModule.fromCustomModel(
      this.opts.modelSource,
      this.opts.tokenizerSource,
      this.opts.tokenizerConfigSource,
      () => {}, // download progress (no-op: sources should be local/pre-cached)
      (_token: string) => {
        this.tokenTimestamps.push(Date.now());
        this.batchCount += 1;
        // interrupt once we've generated ~maxDecodeTokens (batchCount * BATCH)
        if (this.batchCount * ExecutorchAdapter.BATCH >= this.maxDecodeTokens && this.llm) {
          this.llm.interrupt();
        }
      },
    );
    this.llm.configure({
      generationConfig: {
        temperature: 0,
        outputTokenBatchSize: ExecutorchAdapter.BATCH,
        batchTimeInterval: 0,
      },
    });
    return Date.now() - t0;
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    if (!this.llm) throw new Error('executorch: load() first');
    this.tokenTimestamps = [];
    this.batchCount = 0;
    this.maxDecodeTokens = cfg.maxDecodeTokens;

    const t0 = Date.now();
    await this.llm.forward(cfg.prompt); // raw, pre-templated — no chat wrapper
    const tEnd = Date.now();

    if (this.tokenTimestamps.length < 1) {
      throw new Error('executorch: no token callbacks — cannot compute metrics');
    }
    const tFirst = this.tokenTimestamps[0]; // ~time to first batch
    const ttftMs = tFirst - t0;
    const promptTokens = this.llm.getPromptTokensCount();
    const decodeTokens = this.llm.getGeneratedTokenCount(); // engine's own count

    // decode measured over the whole decode phase (total wall-time - prefill),
    // amortizing per-batch bridge overhead rather than paying it per token.
    const decodeMs = Math.max(1, tEnd - tFirst);
    return {
      loadMs: 0, // filled by protocol runner
      ttftMs,
      promptTokens,
      prefillTps: promptTokens > 0 && ttftMs > 0 ? (promptTokens / ttftMs) * 1000 : 0,
      prefillMethod: 'ttft-derived',
      decodeTokens,
      decodeTps: decodeTokens > 1 ? ((decodeTokens - ExecutorchAdapter.BATCH) / decodeMs) * 1000 : 0,
      decodeMethod: 'total-time-derived',
    };
  }

  async release(): Promise<void> {
    if (this.llm) {
      this.llm.delete();
      this.llm = null;
    }
  }
}
