import * as FS from '@dr.pogodin/react-native-fs';
import {initLlama, LlamaContext} from 'llama.rn';
import {runReference} from '../src/harness/reference';
import bundled from '../configs/cpu-reference-v1.json';

const {createHash} = require('crypto');

jest.mock('@dr.pogodin/react-native-fs', () => ({
  ExternalDirectoryPath: '/fixture', exists: jest.fn(), stat: jest.fn(),
  readFile: jest.fn(), writeFile: jest.fn(), hash: jest.fn(), mkdir: jest.fn(),
}));
jest.mock('react-native', () => ({Platform: {OS: 'android', constants: {Model: 'Fixture'}, Version: 35}}));
jest.mock('llama.rn', () => ({initLlama: jest.fn()}));

let files: Map<string, string>;
let output: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  files = new Map([['/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 1, cooldownSec: 0})]]);
  jest.mocked(FS.exists).mockImplementation(async path => files.has(path));
  jest.mocked(FS.stat).mockResolvedValue({size: 100} as FS.StatResultT);
  jest.mocked(FS.readFile).mockImplementation(async path => files.get(path)!);
  jest.mocked(FS.writeFile).mockImplementation(async (path, text) => {files.set(path, text);});
  jest.mocked(FS.hash).mockImplementation(async path => path.endsWith('.gguf') ? 'a'.repeat(64) :
    createHash('sha256').update(files.get(path)!).digest('hex'));
  jest.mocked(initLlama).mockResolvedValue({
    completion: async (_options: unknown, token: () => void) => {
      token();
      return {timings: {prompt_n: 512, predicted_n: 128, prompt_ms: 10, predicted_ms: 20,
        prompt_per_second: 51200, predicted_per_second: 6400}};
    },
    release: jest.fn(async () => {}),
  } as unknown as LlamaContext);
  output = jest.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {output.mockRestore();});

test('configuration reaches CPU engine and checksummed evidence file', async () => {
  await runReference(() => {});
  expect(initLlama).toHaveBeenCalledWith(expect.objectContaining({n_threads: 4, n_ctx: 2048, n_gpu_layers: 0}));
  const marker = output.mock.calls.find(([line]) => line.startsWith('XEBENCH_RESULT_FILE '))![0];
  expect(marker.length).toBeLessThan(256);
  const reference = JSON.parse(marker.slice('XEBENCH_RESULT_FILE '.length));
  const data = files.get(`/fixture/xebench-results/${reference.name}`)!;
  expect(reference.sha256).toBe(createHash('sha256').update(data).digest('hex'));
  const record = JSON.parse(data);
  expect(record.status).toBe('complete');
  expect(record.artifact).toEqual({sha256: 'a'.repeat(64), sizeBytes: 100});
  expect(record.coldRuns[0]).toMatchObject({promptTokens: 512, decodeTokens: 128, status: 'complete'});
  expect(record.engineVersion).toContain('llama.rn');
  expect(record.configuration.nColdRuns).toBe(1);
});

test('invalid staged configuration fails before hashing or native loading', async () => {
  files.set('/fixture/xebench-config.json', JSON.stringify({...bundled, nColdRuns: 0}));
  await expect(runReference(() => {})).rejects.toThrow();
  expect(FS.hash).not.toHaveBeenCalled();
  expect(initLlama).not.toHaveBeenCalled();
});

test('repeated invocations write different evidence filenames', async () => {
  await runReference(() => {});
  await runReference(() => {});
  const paths = jest.mocked(FS.writeFile).mock.calls.map(([path]) => path);
  expect(new Set(paths).size).toBe(2);
});

test('file write failure releases the engine and emits no dangling reference', async () => {
  jest.mocked(FS.writeFile).mockRejectedValueOnce(new Error('fixture disk full'));
  await expect(runReference(() => {})).rejects.toThrow('fixture disk full');
  const context = await jest.mocked(initLlama).mock.results[0].value;
  expect(context.release).toHaveBeenCalled();
  expect(output).not.toHaveBeenCalled();
});
