import type { BackendId, EngineId, SingleRunMetrics } from '../types';

export interface BenchOnceConfig {
  /** Fully chat-templated raw prompt string — identical across engines. */
  prompt: string;
  maxDecodeTokens: number;
}

/**
 * Uniform engine surface for the protocol runner.
 * Contract: load() creates a FRESH engine/model instance; release() destroys it.
 * A cold run in this harness = load() -> benchOnce() -> release() in a warm
 * process (documented distinction from true cold start = app restart).
 */
export interface EngineAdapter {
  readonly engine: EngineId;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  engineVersion(): string;
  /** Engine knobs actually in effect, for the protocol record */
  settings(): { nThreads: number | null; nCtx: number | null };
  load(): Promise<number>; // returns loadMs
  benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics>;
  release(): Promise<void>;
}
