import {LiteRtAdapter} from '../src/harness/adapters/LiteRtAdapter';
import {STANDARD_PROMPT} from '../src/harness/prompts';

const mockGenerate = jest.fn();
const mockLoad = jest.fn();
jest.mock('react-native', () => ({NativeModules: {LiteRtBench: {
  generate: (...args: unknown[]) => mockGenerate(...args), load: (...args: unknown[]) => mockLoad(...args),
  release: jest.fn(),
  getConstants: () => ({litertLmVersion: '0.18.0'}),
}}}));

const timings = {ttftMs: 812, benchmark: {prefillTokens: 520, prefillTps: 640.5, decodeTokens: 128, decodeTps: 41.2}};
const adapter = () => new LiteRtAdapter({modelPath: '/files/gemma3-1b-it-int4.litertlm', backend: 'cpu',
  quantLabel: 'int4', modelLabel: 'Gemma 3 1B', nThreads: 4});

beforeEach(() => {
  mockGenerate.mockReset();
  mockLoad.mockReset();
  mockLoad.mockResolvedValue({loadMs: 300, librariesBefore: [], librariesAfter: []});
});

test('a user-message request goes through the artifact chat template', async () => {
  mockGenerate.mockResolvedValue(timings);
  const metrics = await adapter().benchOnce({prompt: '<templated>', text: 'Summarize', maxDecodeTokens: 128});
  expect(mockGenerate).toHaveBeenCalledWith('Summarize', 128, true, false);
  expect(metrics).toMatchObject({promptTokens: 520, prefillTps: 640.5, decodeTps: 41.2, ttftMs: 812});
});

test('a pre-templated prompt (std-pp512-v1) is prefilled verbatim and must be confirmed', async () => {
  mockGenerate.mockResolvedValue({...timings, rawPromptVerified: true});
  const metrics = await adapter().benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128});
  expect(mockGenerate).toHaveBeenCalledWith(STANDARD_PROMPT, 128, true, true);
  expect(metrics.prefillMethod).toBe('engine-timings');
  mockGenerate.mockResolvedValue(timings);
  await expect(adapter().benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128}))
    .rejects.toThrow('did not confirm the verbatim prompt');
});

test('the output gate always uses the chat template', async () => {
  mockGenerate.mockResolvedValue({text: 'Paris'});
  await expect(adapter().generate({prompt: '<templated>', text: 'Capital?', maxDecodeTokens: 32})).resolves.toBe('Paris');
  expect(mockGenerate).toHaveBeenCalledWith('Capital?', 32, false, false);
});

test('decodeMode fixed: loads after prepareMeasurement create fixed-length engines and must decode that length', async () => {
  const fixed = adapter();
  await fixed.load();
  expect(mockLoad).toHaveBeenLastCalledWith('/files/gemma3-1b-it-int4.litertlm', 'cpu', 4, 0);
  fixed.prepareMeasurement({decodeMode: 'fixed', decodeTokens: 128});
  await fixed.load();
  expect(mockLoad).toHaveBeenLastCalledWith('/files/gemma3-1b-it-int4.litertlm', 'cpu', 4, 128);
  // A reply that stopped at the end of turn (12 tokens) is not a fixed-length measurement.
  mockGenerate.mockResolvedValue({...timings, rawPromptVerified: true, benchmark: {...timings.benchmark, decodeTokens: 12}});
  await expect(fixed.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128})).rejects.toThrow('not the fixed 128');
  mockGenerate.mockResolvedValue({...timings, rawPromptVerified: true, benchmark: {...timings.benchmark, decodeTokens: 128}});
  await expect(fixed.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128})).resolves.toMatchObject({decodeTokens: 128});
  expect(() => fixed.prepareMeasurement({decodeMode: 'fixed', decodeTokens: 0})).toThrow('positive');
});
