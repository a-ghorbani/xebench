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
    use_mmap: boolean; use_mlock: boolean; no_extra_bufts: boolean; flash_attn_type: 'auto' | 'on' | 'off';
    cache_type_k: 'f16'; cache_type_v: 'f16'; devices?: string[];
    /**
     * 0: llama.rn's prompt state cache is off. With it on (llama.rn's default, 160 MiB), a first
     * prompt on a sliding-window model (Gemma 3, Gemma 4) is decoded in a separate one-token batch
     * up to the first message boundary, so llama.cpp's prompt counters (timings.prompt_n,
     * prompt_ms) leave out the BOS token. Absent in records of builds that did not pass it.
     */
    state_cache_budget_mb?: 0;
  };
  /**
   * logit_bias (decodeMode fixed, measured repetitions): [token id, false] for every
   * end-of-generation token of the run (eogTokenIds), llama.rn's form for a bias of -infinity
   * (cpp/jsi/JSIParams.cpp), so decoding never stops before n_predict.
   */
  completion: {n_predict: number; temperature: number; seed: number; logit_bias?: [number, false][]};
  unlisted: 'binding-defaults';
  /**
   * Where the binding came from: the npm package as published, without patches (xebench is
   * reproducible from public packages). PocketPal ships the same version with its own
   * JSI/completion-loop patch; see docs/REFERENCE-RUNS.md.
   */
  distribution: {source: 'npm'; package: 'llama.rn'; version: string; patches: []};
}

/**
 * Every react-native-executorch parameter the harness passes (ExecutorchAdapter); anything
 * unlisted is the binding's default. The runner is created with createLLMRunner (ExecuTorch's
 * TextLLMRunner, text only), and generate() receives the prompt string verbatim.
 */
export interface ExecutorchEffectiveSettings {
  binding: 'react-native-executorch';
  runner: 'createLLMRunner';
  /** RNE's TextLLMRunner load mode (llm_runner.cpp kLoadMode) and the text-only modality list. */
  load: {loadMode: 'mmap'; modalities: []};
  /**
   * GenerationConfig passed to generate(). ignoreEos true (decodeMode fixed) decodes exactly
   * maxNewTokens tokens past the end of turn. numBos/numEos/echo are TextLLMRunner's defaults,
   * which RNE does not change: the prompt is tokenized as given, with no BOS added.
   */
  /** promptPrefix: text put before every prompt (executorch.ts executorchPromptPrefix: "<bos>" for Gemma). */
  generation: {temperature: 0; maxNewTokens: number; ignoreEos: boolean; numBos: 0; numEos: 0; echo: false;
    promptPrefix: '' | '<bos>'};
  /** XNNPACK threadpool size: ExecuTorch's runtime default (RNE sets none). */
  threads: 'runtime-default';
  unlisted: 'binding-defaults';
  distribution: {source: 'npm'; package: 'react-native-executorch'; version: string;
    /** The GitHub release of the prebuilt native libraries (package.json nativeLibsVersion). */
    nativeLibsVersion: string | null; backends: ['xnnpack']; patches: []};
}

export type EffectiveSettings = LlamaRnEffectiveSettings | ExecutorchEffectiveSettings;

/** What effectiveSettings() needs to state the parameters of a request. */
export interface SettingsRequest {
  maxDecodeTokens: number;
  /** Configuration decodeMode (ExecuTorch: ignoreEos on the measured repetitions). */
  decodeMode?: 'fixed';
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
  /** The full parameter set passed to the engine for a request of this size (llama.rn, ExecuTorch). */
  effectiveSettings?(request: SettingsRequest): EffectiveSettings;
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
  /**
   * Called once before the measured repetitions (not before the output gate): with decodeMode
   * fixed, every later load() creates an engine that decodes exactly decodeTokens tokens.
   */
  prepareMeasurement?(options: {decodeMode: 'fixed'; decodeTokens: number}): void;
}
