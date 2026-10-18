import {NativeModules} from 'react-native';
import type {BackendId, SingleRunMetrics} from '../types';
import type {BenchOnceConfig, EngineAdapter} from './EngineAdapter';
import {backendEvidence, BackendEvidence} from '../backend';
import {NO_RUNTIME_VERSIONS, RuntimeVersions, version} from '../runtime';

/**
 * The versions LiteRtBenchModule reports from the APK: the AARs as Gradle resolved them and
 * the QAIRT build string in the packaged libQnnHtp.so. The Qualcomm runtime and QAIRT only
 * take part in NPU runs; other backends record them as null.
 */
export function liteRtRuntimeVersions(constants: unknown, backend: BackendId): RuntimeVersions {
  const value = (constants ?? {}) as Record<string, unknown>;
  const npu = backend === 'npu';
  return {...NO_RUNTIME_VERSIONS, litertLm: version(value.litertLmVersion),
    litertNpuRuntimeQualcomm: npu ? version(value.litertNpuRuntimeQualcommVersion) : null,
    qairt: npu ? version(value.qairtVersion) : null};
}

/** Exported label alphabet: no commas (scripts/export_evidence.py LABEL). */
export const liteRtEngineVersion = (backend: BackendId, versions: RuntimeVersions) => {
  const litert = `litertlm-android ${versions.litertLm ?? 'unknown'}`;
  return backend === 'npu' ? `LiteRT-LM ${litert} + litert-npu-runtime-qualcomm ` +
    `${versions.litertNpuRuntimeQualcomm ?? 'unknown'} (npu)` : `LiteRT-LM ${litert} (${backend})`;
};

export interface LiteRtAdapterOptions {
  /** Absolute path to the .litertlm on device */
  modelPath: string;
  backend: 'cpu' | 'gpu' | 'npu';
  quantLabel: string; // e.g. 'int4'
  modelLabel: string; // e.g. 'Gemma 3 1B'
  /** CPU backend threads; null = LiteRT-LM default. */
  nThreads?: number | null;
}

type Libraries = {before: string[] | null; after: string[] | null};

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every(item => typeof item === 'string') ? value as string[] : null;

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/**
 * LiteRT-LM adapter over the Engine/Conversation API (LiteRtBenchModule.kt).
 *
 * load() creates and initializes a fresh Engine on the requested backend; benchOnce()
 * runs one single-turn greedy conversation on the real prompt (the artifact's own chat
 * template) and reports LiteRT-LM's engine counters (Conversation.getBenchmarkInfo):
 * prefill and decode tok/s are 'engine-timings', like llama.cpp's timings. TTFT is
 * request -> first streamed callback, timed natively on the worker thread.
 */
export class LiteRtAdapter implements EngineAdapter {
  readonly engine = 'litert-lm' as const;
  readonly backend: BackendId;
  readonly quantLabel: string;
  readonly modelLabel: string;
  readonly modelFile: string;
  private opts: LiteRtAdapterOptions;
  private libraries: Libraries | null = null;
  private native: any;

  constructor(opts: LiteRtAdapterOptions) {
    this.native = NativeModules.LiteRtBench;
    if (!this.native) {
      throw new Error('LiteRtBench native module not linked — build the experimental flavor (assembleExperimentalRelease)');
    }
    this.opts = opts;
    this.backend = opts.backend;
    this.quantLabel = opts.quantLabel;
    this.modelLabel = opts.modelLabel;
    this.modelFile = opts.modelPath.split('/').pop() ?? opts.modelPath;
  }

  engineVersion(): string {
    return liteRtEngineVersion(this.opts.backend, this.runtimeVersions());
  }

  runtimeVersions(): RuntimeVersions {
    const constants = typeof this.native.getConstants === 'function' ? this.native.getConstants() : this.native;
    return liteRtRuntimeVersions(constants, this.opts.backend);
  }

  settings() {
    return {nThreads: this.opts.nThreads ?? null, nCtx: null};
  }

  async load(): Promise<number> {
    this.libraries = null;
    const result = await this.native.load(this.opts.modelPath, this.opts.backend, this.opts.nThreads ?? -1);
    this.libraries = {before: strings(result?.librariesBefore), after: strings(result?.librariesAfter)};
    if (!finite(result?.loadMs)) {
      throw new Error('LiteRT-LM load returned no duration');
    }
    return result.loadMs;
  }

  backendEvidence(): BackendEvidence {
    return backendEvidence(this.engine, this.backend, this.libraries ? 'process-libraries' : 'none', null,
      this.libraries);
  }

  async generate(cfg: BenchOnceConfig): Promise<string> {
    const result = await this.native.generate(cfg.text ?? cfg.prompt, cfg.maxDecodeTokens, false);
    if (typeof result?.text !== 'string') {
      throw new Error('LiteRT-LM returned no text');
    }
    return result.text;
  }

  async benchOnce(cfg: BenchOnceConfig): Promise<SingleRunMetrics> {
    if (cfg.text === undefined) {
      throw new Error('LiteRT-LM takes the prompt as a user message (accelerator protocol)');
    }
    const result = await this.native.generate(cfg.text, cfg.maxDecodeTokens, true);
    const info = result?.benchmark;
    if (!info) {
      throw new Error(`LiteRT-LM benchmark info unavailable: ${result?.benchmarkError ?? 'not reported'}`);
    }
    if (!finite(result.ttftMs) || !finite(info.prefillTps) || !finite(info.decodeTps)) {
      throw new Error('LiteRT-LM returned non-finite timings');
    }
    return {
      loadMs: 0, // filled by the protocol runner from load()
      ttftMs: result.ttftMs,
      promptTokens: info.prefillTokens,
      prefillTps: info.prefillTps,
      prefillMethod: 'engine-timings',
      decodeTokens: info.decodeTokens,
      decodeTps: info.decodeTps,
      decodeMethod: 'engine-timings',
    };
  }

  async release(): Promise<void> {
    this.libraries = null;
    await this.native.release();
  }
}
