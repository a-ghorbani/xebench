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
