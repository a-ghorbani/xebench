import type { BackendId, EngineId, SingleRunMetrics } from '../types';
import type { RuntimeVersions } from '../runtime';
import type { BackendEvidence } from '../backend';

export interface BenchOnceConfig {
  /** Fully chat-templated raw prompt string — identical across engines. */
  prompt: string;
  /** The same request as a plain user message, for engines that apply their own template. */
  text?: string;
  maxDecodeTokens: number;
}

/** What llama.rn reports about a loaded context, bounded; anything not reported is null. */
export interface ExecutionReport {
  /** True when the engine placed the model on a GPU-type device. */
  gpu: boolean | null;
  reasonNoGPU: string | null;
  /** Backend devices the model was placed on, as named by the engine (CPU is not listed). */
  devices: string[] | null;
  /** Native library variant the binding loaded, e.g. rnllama_jni_v8_2_dotprod_i8mm. */
  androidLib: string | null;
  /** llama.cpp system_info line: threads in use and compiled CPU backend features. */
  systemInfo: string | null;
  /**
   * The llama.rn native libraries mapped in the process after the load, with their ELF GNU
   * build IDs (40 hex; null when unreadable). Names the exact binary that ran; null when the
   * BuildInfo module could not report.
   */
  nativeLibraries: NativeLibrary[] | null;
}

export interface NativeLibrary {
  /** Library file name, e.g. librnllama_v8_2_dotprod_i8mm_hexagon_opencl.so. */
  name: string;
  buildId: string | null;
}

/** Every llama.rn parameter the harness passes; anything unlisted is the binding's default. */
export interface LlamaRnEffectiveSettings {
  binding: 'llama.rn';
  init: {
    n_ctx: number; n_batch: number; n_ubatch: number; n_threads: number | null; n_gpu_layers: number;
    use_mmap: boolean; use_mlock: boolean; flash_attn_type: 'auto' | 'on' | 'off';
    cache_type_k: 'f16'; cache_type_v: 'f16'; devices?: string[];
  };
  completion: {n_predict: number; temperature: number; seed: number};
  unlisted: 'binding-defaults';
  /**
   * Where the binding came from: the npm package as published, without patches (xebench is
   * reproducible from public packages). PocketPal ships the same version with its own
   * JSI/completion-loop patch; see docs/REFERENCE-RUNS.md.
   */
  distribution: {source: 'npm'; package: 'llama.rn'; version: string; patches: []};
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
  /** The full parameter set passed to the engine for a request of this size (llama.rn only). */
  effectiveSettings?(request: Pick<BenchOnceConfig, 'maxDecodeTokens'>): LlamaRnEffectiveSettings;
  /** What the engine reported about the context created by the last successful load(). */
  executionReport?(): ExecutionReport;
  /** Versions of the runtimes this adapter runs on, from the shipped artifacts (runtime.ts). */
  runtimeVersions?(): RuntimeVersions;
  load(): Promise<number>; // returns loadMs
  benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics>;
  release(): Promise<void>;
  /** Greedy generation on the loaded instance, returning the reply text (correctness gate). */
  generate?(cfg: BenchOnceConfig): Promise<string>;
  /** What the engine or process reported about the backend after the last load(). */
  backendEvidence?(): BackendEvidence;
}
