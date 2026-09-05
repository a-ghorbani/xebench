import {aggregate, ReferenceSession, runSession} from '../src/harness/session';
import {parseConfig} from '../src/harness/config';
import configData from '../configs/cpu-reference-v1.json';
import type {SingleRunMetrics} from '../src/harness/types';

const config = parseConfig(configData);
const identity = {runId: 'fixture-run', timestampIso: '2026-01-01T00:00:00Z',
  deviceInfo: {model: 'Fixture', androidRelease: '16'}, artifact: {sha256: 'a'.repeat(64), sizeBytes: 100}};

function adapter() {
  return {
    engine: 'llama.cpp' as const, backend: 'cpu' as const, modelLabel: 'Llama 3.2 1B',
    quantLabel: 'Q4_K_M', modelFile: config.runs[0].modelFile,
    engineVersion: () => 'fixture-version', settings: () => ({nThreads: 4, nCtx: 2048}),
    load: jest.fn(async () => 17), release: jest.fn(async () => {}),
    benchOnce: jest.fn(async (): Promise<SingleRunMetrics> => ({loadMs: 0, ttftMs: 25,
      promptTokens: 512, decodeTokens: 128, prefillTps: 200, decodeTps: 30,
      prefillMethod: 'engine-timings', decodeMethod: 'engine-timings'})),
  };
}

test('quartiles use documented interpolation and reject non-finite input', () => {
  expect(aggregate([10, 20, 100])).toEqual({median: 20, iqr: 45, min: 10, max: 100, n: 3});
  expect(aggregate([])).toBeNull();
  expect(aggregate([8])).toEqual({median: 8, iqr: 0, min: 8, max: 8, n: 1});
  expect(() => aggregate([NaN])).toThrow();
});

test('retains each repetition and checkpoints before cooldown', async () => {
  const engine = adapter();
  const checkpoints: ReferenceSession[] = [];
  const sleep = jest.fn(async () => {
    expect(checkpoints).toHaveLength(engine.benchOnce.mock.calls.length);
    expect(engine.release).toHaveBeenCalledTimes(engine.benchOnce.mock.calls.length);
  });
  const result = await runSession(engine, config, config.runs[0], identity, async record => {
    checkpoints.push(JSON.parse(JSON.stringify(record)));
  }, sleep);
  expect(checkpoints.map(record => record.status)).toEqual(['running', 'running', 'complete']);
  expect(result.coldRuns.map(run => run.runIndex)).toEqual([0, 1, 2]);
  expect(result.summary.loadMs).toEqual({median: 17, iqr: 0, min: 17, max: 17, n: 3});
  expect(result.coldRuns[0]).toMatchObject({loadMs: 17, promptTokens: 512, decodeTokens: 128});
  expect(result.guardsPassed).toBeNull();
  expect(result.observedBackend).toBeNull();
  expect(sleep.mock.calls).toEqual([[30000], [30000]]);
});

test.each(['load', 'inference', 'release'] as const)('retains failed %s attempt and releases resources', async phase => {
  const engine = adapter();
  const action = phase === 'inference' ? engine.benchOnce : engine[phase];
  action.mockRejectedValueOnce(new Error('fixture failure'));
  const persist = jest.fn(async (_record: ReferenceSession) => {});
  const result = await runSession(engine, config, config.runs[0], identity, persist);
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(result.status).toBe('failed');
  expect(result.coldRuns).toHaveLength(1);
  expect(result.coldRuns[0]).toMatchObject({status: 'failed', error: {phase, message: 'fixture failure'}});
  expect(result.summary.decodeTps).toBeNull();
  expect(persist).toHaveBeenCalledTimes(1);
});

test('a later failure preserves successful repetitions and their actual count', async () => {
  const engine = adapter();
  engine.load.mockResolvedValueOnce(17).mockRejectedValueOnce(new Error('second load failed'));
  const result = await runSession(engine, config, config.runs[0], identity, async () => {}, async () => {});
  expect(result.coldRuns).toHaveLength(2);
  expect(result.status).toBe('failed');
  expect(result.summary.decodeTps?.n).toBe(1);
});

test('invalid metrics are not serialized as valid JSON nulls', async () => {
  const engine = adapter();
  const metrics = await engine.benchOnce();
  engine.benchOnce.mockResolvedValue({...metrics, decodeTps: Infinity});
  const result = await runSession(engine, config, config.runs[0], identity, async () => {});
  expect(result.status).toBe('failed');
  expect(result.summary.decodeTps).toBeNull();
});

test('persistence failure stops further native runs after releasing the current engine', async () => {
  const engine = adapter();
  await expect(runSession(engine, config, config.runs[0], identity, async () => {
    throw new Error('disk full');
  })).rejects.toThrow('disk full');
  expect(engine.release).toHaveBeenCalledTimes(1);
  expect(engine.load).toHaveBeenCalledTimes(1);
});

test('configuration and effective adapter settings must agree before loading', async () => {
  const engine = adapter();
  engine.settings = () => ({nThreads: 8, nCtx: 2048});
  await expect(runSession(engine, config, config.runs[0], identity, async () => {})).rejects.toThrow('does not match');
  expect(engine.load).not.toHaveBeenCalled();
});
