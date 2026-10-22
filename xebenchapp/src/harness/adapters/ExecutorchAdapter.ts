import {NativeModules} from 'react-native';
import {createWorkletRuntime} from 'react-native-worklets';
import {getRegisteredBackends, loadModel, wrapAsync} from 'react-native-executorch';
import {createLLMRunner} from 'react-native-executorch/llm';
import type {LLMRunner} from 'react-native-executorch/llm';
import type {SingleRunMetrics} from '../types';
import type {BenchOnceConfig, EngineAdapter, ExecutorchEffectiveSettings, SettingsRequest} from './EngineAdapter';
import {monotonicNow} from '../clock';
import {backendEvidence, BackendEvidence} from '../backend';
import type {RuntimeVersions} from '../runtime';
import {
  executorchEffectiveSettings, executorchEngineVersion, executorchMetrics, executorchRuntimeVersions, inspection, LIBRARY,
  NAME, ProgramInspection, strings, TimedRequest,
} from '../executorch';

import {nativeLibsVersion, version as RNE_VERSION} from 'react-native-executorch/package.json';

/**
 * ExecutorchBenchModule (experimental flavor): the ET_VERSION of the headers shipped with the
 * native libraries it links (embedded at build time) and the mapped ExecuTorch-family libraries.
 * Other flavors do not link ExecuTorch and have no such module.
 */
interface ExecutorchBenchNative {
  getConstants?: () => {executorchVersion?: unknown};
  executorchVersion?: unknown;
  libraries: () => Promise<unknown>;
}

function inspectProgram(path: string) {
  'worklet';
  // eagerLoadMethods false: method metadata only; no delegate is initialized.
  const model = loadModel(path, {eagerLoadMethods: false});
  try {
    let maxSeqLen: unknown = null;
    try {
      maxSeqLen = model.execute('get_max_seq_len', [], [])[0];
    } catch {
      maxSeqLen = null;
    }
    return {backends: model.backends, schema: model.schema, maxSeqLen};
  } finally {
    model.dispose();
  }
}

/** One request on the worklet thread: steady clock around generate(), the first callback timestamped. */
function timedGenerate(runner: LLMRunner, prompt: string,
  config: {temperature: number; maxNewTokens: number; ignoreEos: boolean}): TimedRequest & {text: string} {
  'worklet';
  // Worklet runtimes provide performance.now() on std::chrono::steady_clock, like the RN runtime.
  const clock = (globalThis as unknown as {performance: {now(): number}}).performance;
  // A fresh KV position for every request (the gate sends several on one instance).
  runner.reset();
  let first = -1;
  let emitted = 0;
  let text = '';
  const start = clock.now();
  const stats = runner.generate(prompt, config, (piece: string) => {
    if (first < 0) first = clock.now();
    emitted += 1;
    text += piece;
  });
  const end = clock.now();
  return {stats: {numPromptTokens: stats.numPromptTokens, numGeneratedTokens: stats.numGeneratedTokens,
    firstTokenMs: stats.firstTokenMs, inferenceStartMs: stats.inferenceStartMs, inferenceEndMs: stats.inferenceEndMs},
  start, first, end, emitted, text};
}

let runtime: ReturnType<typeof createWorkletRuntime> | null = null;
/** One dedicated worklet thread for ExecuTorch work, created when the first adapter needs it. */
const executorchRuntime = () => runtime ?? (runtime = createWorkletRuntime({name: 'XebenchExecuTorch'}));

export interface ExecutorchAdapterOptions {
  /** Absolute path to the .pte on device */
  modelPath: string;
  /** Absolute path to its tokenizer.json */
  tokenizerPath: string;
  quantLabel: string;
  modelLabel: string;
}

/**
 * react-native-executorch 0.10 adapter over its low-level LLM runner (createLLMRunner, ExecuTorch's
 * TextLLMRunner) with the XNNPACK backend, on a dedicated worklet thread.
 *
 * - The prompt string goes to generate() verbatim: no chat template, no BOS added (TextLLMRunner's
 *   num_bos 0), greedy (temperature 0 is passed: ExecuTorch's default is 0.8).
 * - The runner chunks the prefill: a prompt longer than the program's token-input bound (512 for
 *   the Gemma 4 E2B export) takes several forwards. The bound and the number of forwards are
 *   recorded per request (executorchStats).
 * - decodeMode fixed: ignoreEos with maxNewTokens N decodes exactly N tokens (the first sampled by
 *   the prefill, N - 1 by the decode loop), past the end of turn.
 * - load() creates the runner (program load, method init with the XNNPACK delegate, tokenizer);
 *   release() disposes it. Each load is a fresh instance.
 */
export class ExecutorchAdapter implements EngineAdapter {
  readonly engine = 'executorch' as const;
  readonly backend = 'cpu' as const; // XNNPACK
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: ExecutorchAdapterOptions;
  private native: ExecutorchBenchNative;
  private runner: LLMRunner | null = null;
  private program: ProgramInspection | null = null;
  private evidence: BackendEvidence | null = null;
  /** decodeMode fixed: the decode length of every later measured request (0 = until end of turn). */
  private fixedDecodeTokens = 0;

  constructor(opts: ExecutorchAdapterOptions) {
    const module = NativeModules.ExecutorchBench as ExecutorchBenchNative | undefined;
    if (!module) {
      throw new Error('ExecutorchBench native module not linked — build the experimental flavor');
    }
    this.native = module;
    this.opts = opts;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  runtimeVersions(): RuntimeVersions {
    const constants = typeof this.native.getConstants === 'function' ? this.native.getConstants() : this.native;
    return executorchRuntimeVersions(constants, RNE_VERSION);
  }

  engineVersion(): string {
    return executorchEngineVersion(this.runtimeVersions());
  }

  settings() {
    // The context is fixed by the export; the XNNPACK threadpool size is ExecuTorch's default.
    return {nThreads: null, nCtx: null};
  }

  effectiveSettings(request: SettingsRequest): ExecutorchEffectiveSettings {
    return executorchEffectiveSettings(request, {version: RNE_VERSION, nativeLibsVersion});
  }

  prepareMeasurement(options: {decodeMode: 'fixed'; decodeTokens: number}) {
    if (options.decodeMode !== 'fixed' || !Number.isInteger(options.decodeTokens) || options.decodeTokens < 1) {
      throw new Error('ExecuTorch fixed decoding needs a positive decode length');
    }
    this.fixedDecodeTokens = options.decodeTokens;
  }

  private async libraries(): Promise<string[] | null> {
    try {
      return strings(await this.native.libraries(), LIBRARY);
    } catch {
      return null;
    }
  }

  async load(): Promise<number> {
    this.evidence = null;
    if (this.program === null) {
      // Once per adapter, before any timed load: the program's delegates and prefill bound.
      this.program = inspection(await wrapAsync(inspectProgram, executorchRuntime())(this.opts.modelPath)
        .catch(() => null));
    }
    const before = await this.libraries();
    const t0 = monotonicNow();
    this.runner = await wrapAsync(createLLMRunner, executorchRuntime())(this.opts.modelPath, this.opts.tokenizerPath);
    const loadMs = monotonicNow() - t0;
    let registered: string[] | null;
    try {
      registered = strings(getRegisteredBackends(), NAME, 64);
    } catch {
      registered = null;
    }
    this.evidence = backendEvidence(this.engine, this.backend, 'program-delegates', null,
      {before, after: await this.libraries()}, {methods: this.program.methods, registeredBackends: registered});
    return loadMs;
  }

  backendEvidence(): BackendEvidence {
    return this.evidence ?? backendEvidence(this.engine, this.backend, 'none', null, null);
  }

  private async request(prompt: string, maxNewTokens: number, ignoreEos: boolean) {
    if (!this.runner) throw new Error('executorch: load() first');
    return wrapAsync(timedGenerate, executorchRuntime())(this.runner, prompt, {temperature: 0, maxNewTokens, ignoreEos});
  }

  /** Greedy reply for the correctness gate: the engine's own stopping rule (end of turn or budget). */
  async generate(cfg: BenchOnceConfig): Promise<string> {
    return (await this.request(cfg.prompt, cfg.maxDecodeTokens, false)).text;
  }

  /** cfg.prompt is already templated (or the raw std-pp512-v1 text) and goes to the runner verbatim. */
  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    const fixed = this.fixedDecodeTokens > 0;
    if (fixed && cfg.maxDecodeTokens !== this.fixedDecodeTokens) {
      throw new Error('ExecuTorch fixed decoding length disagrees with the request');
    }
    const result = await this.request(cfg.prompt, cfg.maxDecodeTokens, fixed);
    return executorchMetrics(result, this.program?.prefillChunkTokens ?? null, this.fixedDecodeTokens);
  }

  async release(): Promise<void> {
    this.evidence = null;
    if (this.runner) {
      const runner = this.runner;
      this.runner = null;
      runner.dispose();
    }
  }
}
