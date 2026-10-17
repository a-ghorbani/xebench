export interface ReferenceRun {
  id: string;
  engine: 'llama.cpp';
  model: string;
  modelFile: string;
  quant: string;
  nThreads: number;
  nCtx: number;
}

export interface ReferenceConfig {
  schemaVersion: 1;
  id: string;
  nColdRuns: number;
  cooldownSec: number;
  /** Bound on waiting for readiness before each repetition; a timeout skips it. */
  readinessTimeoutSec: number;
  /** Readiness bound on battery-temperature rise over the first ready reading (default 2 °C). */
  maxBatteryTempRiseC: number;
  maxDecodeTokens: number;
  /** Optional fixed run-order seed; otherwise a seed is generated and recorded. */
  orderSeed?: number;
  runs: ReferenceRun[];
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Configuration must contain objects');
  }
  return value as Record<string, unknown>;
};

function fields(value: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) {
    throw new Error('Unknown configuration field');
  }
}

function integer(value: unknown, low: number, high: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < low || value > high) {
    throw new Error(`Expected integer between ${low} and ${high}`);
  }
  return value;
}

function decimal(value: unknown, low: number, high: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high) {
    throw new Error(`Expected number between ${low} and ${high}`);
  }
  return value;
}

function label(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 128 || /[\x00-\x1f]/.test(value)) {
    throw new Error('Invalid configuration label');
  }
  return value;
}

export function parseConfig(input: unknown): ReferenceConfig {
  const value = object(input);
  fields(value, ['schemaVersion', 'id', 'nColdRuns', 'cooldownSec', 'readinessTimeoutSec', 'maxBatteryTempRiseC',
    'maxDecodeTokens', 'orderSeed', 'runs']);
  if (value.schemaVersion !== 1) {
    throw new Error('Unsupported configuration version');
  }
  if (!Array.isArray(value.runs) || !value.runs.length || value.runs.length > 16) {
    throw new Error('Expected 1–16 runs');
  }
  const runs = value.runs.map(item => {
    const run = object(item);
    fields(run, ['id', 'engine', 'model', 'modelFile', 'quant', 'nThreads', 'nCtx']);
    if (run.engine !== 'llama.cpp') {
      throw new Error('This reference protocol supports llama.cpp CPU only');
    }
    const modelFile = label(run.modelFile);
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.gguf$/.test(modelFile)) {
      throw new Error('modelFile must be a GGUF basename, not a path or URL');
    }
    return {
      id: label(run.id), engine: 'llama.cpp' as const, model: label(run.model),
      modelFile, quant: label(run.quant), nThreads: integer(run.nThreads, 1, 32),
      nCtx: integer(run.nCtx, 1024, 32768),
    };
  });
  if (new Set(runs.map(run => run.id)).size !== runs.length) {
    throw new Error('Run IDs must be unique');
  }
  return {
    schemaVersion: 1, id: label(value.id), nColdRuns: integer(value.nColdRuns, 1, 20),
    cooldownSec: integer(value.cooldownSec, 0, 300),
    readinessTimeoutSec: value.readinessTimeoutSec === undefined ? 180 : integer(value.readinessTimeoutSec, 10, 900),
    maxBatteryTempRiseC: value.maxBatteryTempRiseC === undefined ? 2 : decimal(value.maxBatteryTempRiseC, 0.5, 10),
    maxDecodeTokens: integer(value.maxDecodeTokens, 1, 512),
    ...(value.orderSeed === undefined ? {} : {orderSeed: integer(value.orderSeed, 0, 0xffffffff)}), runs,
  };
}

// --- accelerator-v1 configurations (schemaVersion 2) ---------------------------
// docs/experiments/litert-accelerators.md. Every field is explicit (no defaults), so
// the configuration echoed into each record is exactly what was parsed.

export type AcceleratorEngine = 'llama.cpp' | 'litert-lm';
export type AcceleratorBackend = 'cpu' | 'gpu' | 'npu';

export interface AcceleratorRun {
  id: string;
  engine: AcceleratorEngine;
  backend: AcceleratorBackend;
  /** bug-repro: measured only as evidence for an upstream bug; never valid for comparison. */
  purpose: 'measurement' | 'bug-repro';
  model: string;
  modelFile: string;
  quant: string;
  /** CPU threads; null = engine default (LiteRT-LM GPU/NPU runs). */
  nThreads: number | null;
  /** llama.cpp context size; null for LiteRT-LM (fixed by the artifact). */
  nCtx: number | null;
  /** llama.cpp only: layers offloaded (0 = CPU). */
  nGpuLayers?: number;
  /** llama.cpp only: ggml devices, e.g. ["HTP0"] for Hexagon. */
  devices?: string[];
  /** The CPU run of the same engine whose gate outputs this run is checked against. */
  referenceRunId: string | null;
}

export interface AcceleratorConfig {
  schemaVersion: 2;
  id: string;
  nColdRuns: number;
  cooldownSec: number;
  readinessTimeoutSec: number;
  /** Readiness battery-temperature rise bound (run-conditions-v2 default 2 °C), explicit here. */
  maxBatteryTempRiseC: number;
  maxDecodeTokens: number;
  /** Chat template the harness applies for raw-prompt engines (llama.cpp). */
  chatTemplate: 'gemma3';
  orderSeed?: number;
  runs: AcceleratorRun[];
}

const BASENAME = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;
const RUN_FIELDS = ['id', 'engine', 'backend', 'purpose', 'model', 'modelFile', 'quant', 'nThreads', 'nCtx',
  'referenceRunId'];

function oneOf<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) {
    throw new Error(`${what} must be one of ${allowed.join(', ')}`);
  }
  return value as T;
}

function nullableInteger(value: unknown, low: number, high: number): number | null {
  return value === null ? null : integer(value, low, high);
}

function acceleratorRun(item: unknown): AcceleratorRun {
  const run = object(item);
  fields(run, [...RUN_FIELDS, 'nGpuLayers', 'devices']);
  if (RUN_FIELDS.some(key => !(key in run))) {
    throw new Error('Every accelerator run field is required');
  }
  const engine = oneOf(run.engine, ['llama.cpp', 'litert-lm'] as const, 'engine');
  const backend = oneOf(run.backend, ['cpu', 'gpu', 'npu'] as const, 'backend');
  const modelFile = label(run.modelFile);
  const extension = engine === 'llama.cpp' ? '.gguf' : '.litertlm';
  if (!BASENAME.test(modelFile) || !modelFile.endsWith(extension)) {
    throw new Error(`modelFile must be a ${extension} basename, not a path or URL`);
  }
  const result: AcceleratorRun = {
    id: label(run.id), engine, backend, purpose: oneOf(run.purpose, ['measurement', 'bug-repro'] as const, 'purpose'),
    model: label(run.model), modelFile, quant: label(run.quant),
    nThreads: nullableInteger(run.nThreads, 1, 32), nCtx: nullableInteger(run.nCtx, 1024, 32768),
    referenceRunId: run.referenceRunId === null ? null : label(run.referenceRunId),
  };
  if (engine === 'llama.cpp') {
    if (result.nThreads === null || result.nCtx === null) {
      throw new Error('llama.cpp runs need nThreads and nCtx');
    }
    const layers = integer(run.nGpuLayers, 0, 999);
    const devices = run.devices;
    if (devices !== undefined && (!Array.isArray(devices) || !devices.length || devices.length > 4 ||
        devices.some(device => typeof device !== 'string' || !/^[A-Za-z0-9]{1,32}$/.test(device)))) {
      throw new Error('devices must be 1-4 ggml device names');
    }
    const names = (devices ?? []) as string[];
    const htp = names.filter(device => device.toUpperCase().startsWith('HTP')).length;
    const consistent = backend === 'cpu' ? layers === 0 && devices === undefined :
      backend === 'gpu' ? layers > 0 && htp === 0 : layers > 0 && htp > 0 && htp === names.length;
    if (!consistent) {
      throw new Error('nGpuLayers/devices do not match the llama.cpp backend');
    }
    result.nGpuLayers = layers;
    if (devices !== undefined) {
      result.devices = names;
    }
  } else {
    if ('nGpuLayers' in run || 'devices' in run) {
      throw new Error('nGpuLayers/devices apply to llama.cpp only');
    }
    if (result.nCtx !== null) {
      throw new Error('LiteRT-LM runs take nCtx null (fixed by the artifact)');
    }
    if (backend !== 'cpu' && result.nThreads !== null) {
      throw new Error('nThreads applies to LiteRT-LM CPU only');
    }
  }
  return result;
}

export function parseAcceleratorConfig(input: unknown): AcceleratorConfig {
  const value = object(input);
  fields(value, ['schemaVersion', 'id', 'nColdRuns', 'cooldownSec', 'readinessTimeoutSec',
    'maxBatteryTempRiseC', 'maxDecodeTokens', 'chatTemplate', 'orderSeed', 'runs']);
  if (value.schemaVersion !== 2) {
    throw new Error('Unsupported configuration version');
  }
  if (!Array.isArray(value.runs) || !value.runs.length || value.runs.length > 16) {
    throw new Error('Expected 1–16 runs');
  }
  const runs = value.runs.map(acceleratorRun);
  if (new Set(runs.map(run => run.id)).size !== runs.length) {
    throw new Error('Run IDs must be unique');
  }
  for (const run of runs) {
    const reference = runs.find(item => item.id === run.referenceRunId);
    if (run.backend === 'cpu' ? run.referenceRunId !== null :
      !reference || reference.backend !== 'cpu' || reference.engine !== run.engine) {
      throw new Error('GPU/NPU runs need referenceRunId naming a CPU run of the same engine; CPU runs take null');
    }
  }
  return {
    schemaVersion: 2, id: label(value.id), nColdRuns: integer(value.nColdRuns, 1, 20),
    cooldownSec: integer(value.cooldownSec, 0, 300), readinessTimeoutSec: integer(value.readinessTimeoutSec, 10, 900),
    maxBatteryTempRiseC: decimal(value.maxBatteryTempRiseC, 0.5, 10),
    maxDecodeTokens: integer(value.maxDecodeTokens, 1, 512),
    chatTemplate: oneOf(value.chatTemplate, ['gemma3'] as const, 'chatTemplate'),
    ...(value.orderSeed === undefined ? {} : {orderSeed: integer(value.orderSeed, 0, 0xffffffff)}), runs,
  };
}
