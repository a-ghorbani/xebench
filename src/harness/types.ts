/**
 * xebench v0.1 — cross-engine on-device LLM benchmark harness types.
 *
 * Two layers:
 *  1. Raw records (this harness's full-fidelity output, one JSONL line per engine session)
 *  2. EngineBenchmarkRow — mirrors pocketpal-website/lib/engines/types.ts exactly;
 *     produced by scripts/aggregate.mjs from raw records.
 */

export const HARNESS_VERSION = 'xebench-0.1';

export type EngineId = 'llama.cpp' | 'executorch';
export type BackendId = 'cpu' | 'gpu' | 'npu' | 'ane';
export type PlatformId = 'android' | 'ios' | 'macos';

/** How a throughput number was obtained — first-class, per methodology rules. */
export type MeasureMethod =
  | 'engine-timings' // engine's internal counters (llama.cpp timings block)
  | 'ttft-derived' // promptTokens / (firstToken - start); includes 1 decode step + callback overhead
  | 'callback-derived'; // (nTokens - 1) / (lastTokenTs - firstTokenTs) from JS-side token callbacks

export interface ThermalSnapshot {
  /** PowerManager.THERMAL_STATUS_* (0=NONE..6=SHUTDOWN), -1 if unavailable */
  thermalStatus: number;
  /** PowerManager.getThermalHeadroom(0); 1.0 ≈ severe throttling point. null if unavailable */
  thermalHeadroom: number | null;
  /** Battery temperature in °C (proxy for device temp), null if unavailable */
  batteryTempC: number | null;
  batteryPct: number;
  charging: boolean;
  powerSaveMode: boolean;
  timestampMs: number;
}

export interface MemorySnapshot {
  /** Total PSS of the process in MB (Debug.MemoryInfo.getTotalPss) */
  totalPssMb: number;
  /** Peak resident set size of process lifetime in MB (/proc/self/status VmHWM) */
  vmHwmMb: number;
  timestampMs: number;
}

export interface DeviceInfo {
  manufacturer: string;
  model: string;
  device: string;
  /** Build.SOC_MODEL on API 31+, else Build.HARDWARE */
  soc: string;
  androidSdk: number;
  androidRelease: string;
  cpuCores: number;
  totalMemMb: number;
}

export interface SingleRunMetrics {
  /** Wall-clock model + context load, ms */
  loadMs: number;
  /** JS-side: completion request start -> first token callback, ms.
   *  Comparable across engines (same measurement point). */
  ttftMs: number;
  promptTokens: number;
  prefillTps: number;
  prefillMethod: MeasureMethod;
  decodeTokens: number;
  decodeTps: number;
  decodeMethod: MeasureMethod;
  /** Engine-internal numbers when available (llama.cpp), for cross-checking */
  engineInternal?: {
    promptMs: number;
    promptPerSecond: number;
    predictedMs: number;
    predictedPerSecond: number;
  };
}

export interface ColdRunRecord extends SingleRunMetrics {
  runIndex: number;
  /** PSS right before engine load */
  baselinePssMb: number;
  /** Max PSS sampled at 500 ms during load + inference */
  peakPssMb: number;
  /** peakPssMb - baselinePssMb: the engine+model+KV footprint */
  deltaPssMb: number;
  vmHwmMb: number;
  thermalBefore: ThermalSnapshot;
  thermalAfter: ThermalSnapshot;
}

export interface SustainedIteration {
  /** Seconds since sustained phase start */
  tSec: number;
  decodeTps: number;
  ttftMs: number;
  thermalStatus: number;
  thermalHeadroom: number | null;
  batteryTempC: number | null;
}

export interface SustainedResult {
  durationSec: number;
  iterations: SustainedIteration[];
  /** Median decode tok/s over the first 60 s of iterations */
  firstMinuteTgTps: number;
  /** Median decode tok/s over the last 60 s of iterations */
  lastMinuteTgTps: number;
  /** (first - last) / first * 100 */
  degradationPct: number;
  thermalBefore: ThermalSnapshot;
  thermalAfter: ThermalSnapshot;
}

export interface Aggregate {
  median: number;
  iqr: number;
  min: number;
  max: number;
  n: number;
}

/** One JSONL line per engine benchmark session — the raw export. */
export interface EngineSessionRecord {
  schema: 'xebench-raw';
  harnessVersion: string;
  runId: string;
  timestampIso: string;
  engine: EngineId;
  engineVersion: string;
  backend: BackendId;
  platform: PlatformId;
  deviceInfo: DeviceInfo;
  /** Canonical model label matching pocketpal-website CANONICAL_MODELS */
  model: string;
  /** Quant as stated, schemes differ per engine — first-class column */
  quant: string;
  modelFile: string;
  modelFileSizeMb: number | null;
  /** Protocol knobs actually used */
  protocol: {
    nColdRuns: number;
    promptLabel: string;
    promptChars: number;
    maxDecodeTokens: number;
    cooldownSec: number;
    sustainedTargetSec: number;
    nThreads: number | null;
    nCtx: number | null;
    coldDefinition: string;
  };
  coldRuns: ColdRunRecord[];
  summary: {
    ttftMs: Aggregate;
    prefillTps: Aggregate;
    decodeTps: Aggregate;
    peakPssMb: Aggregate;
    deltaPssMb: Aggregate;
    loadMs: Aggregate;
  };
  sustained: SustainedResult | null;
  /** True if another engine ran in this same process before this session
   *  (contaminates VmHWM; PSS-delta stays valid) */
  processReused: boolean;
  guardsPassed: boolean;
  guardNotes: string[];
}

/**
 * Mirror of pocketpal-website/lib/engines/types.ts EngineBenchmarkRow.
 * aggregate.mjs emits this shape (+ documented additive extension fields).
 */
export interface EngineBenchmarkRow {
  engine: string;
  backend: BackendId;
  platform: PlatformId;
  device: string;
  soc?: string;
  model: string;
  quant?: string;
  ppTps?: number | null;
  tgTps?: number | null;
  ttftMs?: number | null;
  memoryMb?: number | null;
  provenance: 'measured' | 'vendor' | 'community';
  sourceName: string;
  sourceUrl?: string;
  sourceDate?: string;
  sampleCount?: number;
  asStated?: string;
}
