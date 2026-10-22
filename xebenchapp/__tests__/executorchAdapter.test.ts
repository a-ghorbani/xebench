/**
 * ExecuTorch (react-native-executorch 0.10, XNNPACK): the adapter over createLLMRunner, with the
 * native pieces (worklet runtime, JSI runner, ExecutorchBenchModule) replaced by fakes that
 * behave like ExecuTorch 1.4.1's TextLLMRunner.
 */
import {executorchMetrics, forwardTokenBound, inspection, programMethods} from '../src/harness/executorch';
import {assessBackend} from '../src/harness/backend';
import {STANDARD_PROMPT} from '../src/harness/prompts';
import {version as RNE_VERSION} from 'react-native-executorch/package.json';

const mockLibraries = jest.fn();
const mockRegistered = jest.fn();
const mockGenerate = jest.fn();
const mockDispose = jest.fn();
const mockModel = jest.fn();
const mockNative: {ExecutorchBench?: object} = {};

jest.mock('react-native', () => ({NativeModules: new Proxy({}, {get: (_target, key) =>
  (mockNative as Record<string | symbol, unknown>)[key]})}));
// reference.ts imports for its other engines; not under test here.
jest.mock('@dr.pogodin/react-native-fs', () => ({}));
jest.mock('llama.rn', () => ({}));
jest.mock('react-native-worklets', () => ({createWorkletRuntime: () => ({name: 'fake'})}));
jest.mock('react-native-executorch', () => ({
  // The fake runs the worklet on the calling thread; errors keep their message as RNE's wrapper does.
  wrapAsync: (fn: (...args: unknown[]) => unknown) => async (...args: unknown[]) => fn(...args),
  loadModel: (...args: unknown[]) => mockModel(...args),
  getRegisteredBackends: () => mockRegistered(),
}));
jest.mock('react-native-executorch/llm', () => ({
  createLLMRunner: (modelPath: string, tokenizerPath: string) => ({modelPath, tokenizerPath,
    reset: jest.fn(), dispose: mockDispose,
    generate: (prompt: string, config: object, onToken: (piece: string) => void) =>
      mockGenerate(prompt, config, onToken)}),
}));

/** TextLLMRunner: the prefill samples token 1, the loop the rest, up to maxNewTokens (or end of turn). */
function runner(promptTokens = 530, endOfTurnAfter = 40) {
  return (_prompt: string, config: {maxNewTokens: number; ignoreEos: boolean}, onToken: (piece: string) => void) => {
    const total = config.ignoreEos ? config.maxNewTokens : Math.min(config.maxNewTokens, endOfTurnAfter);
    for (let index = 0; index < total; index++) onToken(index === 0 ? 'Measurement' : ' x');
    return {numPromptTokens: promptTokens, numGeneratedTokens: total - 1, inferenceStartMs: 1767225600000,
      firstTokenMs: 1767225601060, inferenceEndMs: 1767225601060 + (total - 1) * 50, modelLoadStartMs: 0,
      modelLoadEndMs: 0};
  };
}

const forwardSchema = (dim: object) => ({forward: {inputs: [
  {kind: 'Tensor', dtype: 'int64', shape: [{kind: 'constant', value: 1}, dim]},
  {kind: 'Tensor', dtype: 'int64', shape: [{kind: 'constant', value: 1}]}], outputs: [], runtimeConstraints: []}});

beforeEach(() => {
  jest.resetModules();
  mockNative.ExecutorchBench = {getConstants: () => ({executorchVersion: '1.4.1'}), libraries: mockLibraries};
  mockLibraries.mockResolvedValue(['libxnnpack_executorch_backend.so', 'libexecutorch.so',
    'libRnExecutorch.so']);
  mockRegistered.mockReturnValue(['XnnpackBackend']);
  mockGenerate.mockReset().mockImplementation(runner());
  mockDispose.mockReset();
  mockModel.mockReset().mockImplementation(() => ({
    backends: {forward: ['XnnpackBackend'], get_max_seq_len: []},
    schema: forwardSchema({kind: 'range', range: {min: 1, max: 512, step: 1}}),
    execute: (name: string) => (name === 'get_max_seq_len' ? [2048] : []), dispose: jest.fn()}));
});

const adapter = () => {
  const {ExecutorchAdapter} = require('../src/harness/adapters/ExecutorchAdapter');
  return new ExecutorchAdapter({modelPath: '/files/gemma_4_e2b_xnnpack_8da4w.pte',
    tokenizerPath: '/files/tokenizer.json', quantLabel: '8da4w', modelLabel: 'Gemma 4 E2B',
    promptPrefix: '<bos>'});
};

test('fixed decoding: the verbatim prompt, greedy, exactly the configured tokens past the end of turn', async () => {
  const engine = adapter();
  engine.prepareMeasurement({decodeMode: 'fixed', decodeTokens: 128});
  await engine.load();
  const metrics = await engine.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128});
  // Gemma: the literal <bos> llama.cpp adds as a token goes first (TextLLMRunner adds none).
  expect(mockGenerate).toHaveBeenCalledWith(`<bos>${STANDARD_PROMPT}`, {temperature: 0, maxNewTokens: 128, ignoreEos: true},
    expect.any(Function));
  expect(metrics).toMatchObject({promptTokens: 530, decodeTokens: 128, prefillMethod: 'engine-timings',
    decodeMethod: 'engine-timings', prefillTps: 500, decodeTps: 20,
    engineInternal: {promptMs: 1060, promptPerSecond: 500, predictedMs: 6350, predictedPerSecond: 20},
    executorchStats: {numPromptTokens: 530, numGeneratedTokens: 127, emittedTokens: 128, prefillChunkTokens: 512,
      prefillChunks: 2}});
  expect(metrics.ttftMs).toBeGreaterThanOrEqual(0);
  // A runner that stops at the end of turn cannot pass as a fixed-length decode.
  mockGenerate.mockImplementation((prompt, config, onToken) => runner()(prompt, {...config, ignoreEos: false}, onToken));
  await expect(engine.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128})).rejects.toThrow('not the fixed 128');
  await engine.release();
  expect(mockDispose).toHaveBeenCalledTimes(1);
});

test('the output gate keeps the engine stopping rule', async () => {
  const engine = adapter();
  engine.prepareMeasurement({decodeMode: 'fixed', decodeTokens: 128});
  await engine.load();
  expect(await engine.generate({prompt: '<prompt>', maxDecodeTokens: 32})).toBe('Measurement' + ' x'.repeat(31));
  expect(mockGenerate).toHaveBeenCalledWith('<bos><prompt>', {temperature: 0, maxNewTokens: 32, ignoreEos: false},
    expect.any(Function));
});

test('the prompt prefix follows the chat template: a literal <bos> for Gemma, nothing otherwise', () => {
  const {executorchPromptPrefix} = require('../src/harness/executorch');
  expect(executorchPromptPrefix('gemma3')).toBe('<bos>');
  expect(executorchPromptPrefix('gemma4')).toBe('<bos>');
  expect(executorchPromptPrefix('qwen25')).toBe('');
  expect(executorchPromptPrefix('llama32')).toBe('');
});

test('versions come from the native module and the installed package; settings state every parameter', () => {
  const engine = adapter();
  expect(engine.runtimeVersions()).toMatchObject({executorch: '1.4.1', reactNativeExecutorch: RNE_VERSION,
    llamaCpp: null, litertLm: null});
  expect(engine.engineVersion()).toBe(`ExecuTorch 1.4.1 + react-native-executorch ${RNE_VERSION} (xnnpack)`);
  expect(engine.settings()).toEqual({nThreads: null, nCtx: null});
  expect(engine.effectiveSettings({maxDecodeTokens: 128, decodeMode: 'fixed'})).toMatchObject({
    binding: 'react-native-executorch', generation: {temperature: 0, maxNewTokens: 128, ignoreEos: true, numBos: 0,
      promptPrefix: '<bos>'},
    distribution: {package: 'react-native-executorch', version: RNE_VERSION, backends: ['xnnpack'], patches: []}});
  mockNative.ExecutorchBench = {getConstants: () => ({executorchVersion: ''}), libraries: mockLibraries};
  expect(adapter().runtimeVersions().executorch).toBeNull();
});

test('backend evidence: an XNNPACK-only program on a registered XNNPACK backend is confirmed CPU', async () => {
  const engine = adapter();
  await engine.load();
  expect(engine.backendEvidence()).toEqual({status: 'confirmed', source: 'program-delegates', report: null,
    libraries: {before: ['libRnExecutorch.so', 'libexecutorch.so', 'libxnnpack_executorch_backend.so'],
      after: ['libRnExecutorch.so', 'libexecutorch.so', 'libxnnpack_executorch_backend.so']},
    program: {methods: [{name: 'forward', delegates: ['XnnpackBackend']}, {name: 'get_max_seq_len', delegates: []}],
      registeredBackends: ['XnnpackBackend']}});
  const program = (delegates: string[], registered: string[] | null) =>
    ({methods: [{name: 'forward', delegates}], registeredBackends: registered});
  const libraries = (after: string[] | null) => ({before: null, after});
  const assess = (delegates: string[], registered: string[] | null, after: string[] | null) =>
    assessBackend('executorch', 'cpu', {report: null, libraries: libraries(after), program: program(delegates, registered)});
  expect(assess(['VulkanBackend'], ['XnnpackBackend', 'VulkanBackend'], [])).toBe('mismatch');
  expect(assess(['XnnpackBackend'], null, [])).toBe('unknown');
  expect(assess(['XnnpackBackend'], ['XnnpackBackend'], null)).toBe('unknown');
  expect(assess(['XnnpackBackend'], ['XnnpackBackend'], ['libvulkan_executorch_backend.so'])).toBe('unknown');
  expect(assess(['XnnpackBackend'], ['XnnpackBackend'], ['libxnnpack_executorch_backend.so'])).toBe('confirmed');
  expect(assessBackend('executorch', 'cpu', {report: null, libraries: null, program: null})).toBe('unknown');
  // An unreadable program leaves the evidence unknown; the run is still measured.
  mockModel.mockImplementation(() => {
    throw new Error('schema mismatch');
  });
  const blind = adapter();
  await blind.load();
  expect(blind.backendEvidence().status).toBe('unknown');
  expect((await blind.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: 128})).executorchStats)
    .toMatchObject({prefillChunkTokens: null, prefillChunks: null});
});

test('program inspection: the prefill bound is the smaller of the graph bound and get_max_seq_len', () => {
  expect(forwardTokenBound(forwardSchema({kind: 'constant', value: 128}))).toBe(128);
  expect(forwardTokenBound(forwardSchema({kind: 'range', range: {min: 1, max: 511, step: 1}}))).toBe(511);
  expect(forwardTokenBound(forwardSchema({kind: 'enum', choices: [1, 64, 256]}))).toBe(256);
  expect(forwardTokenBound({forward: {inputs: []}})).toBeNull();
  expect(forwardTokenBound(null)).toBeNull();
  const schema = forwardSchema({kind: 'range', range: {min: 1, max: 512, step: 1}});
  expect(inspection({backends: {}, schema, maxSeqLen: 256}).prefillChunkTokens).toBe(256);
  expect(inspection({backends: {}, schema, maxSeqLen: null}).prefillChunkTokens).toBe(512);
  expect(inspection(null)).toEqual({methods: null, prefillChunkTokens: null});
  expect(programMethods({b: ['XnnpackBackend'], a: []})).toEqual([{name: 'a', delegates: []},
    {name: 'b', delegates: ['XnnpackBackend']}]);
  expect(programMethods({forward: 'XnnpackBackend'})).toBeNull();
  expect(programMethods(['forward'])).toBeNull();
});

test('metrics are refused when the statistics cannot support them', () => {
  const ok = {stats: {numPromptTokens: 520, numGeneratedTokens: 127, inferenceStartMs: 1000, firstTokenMs: 2000,
    inferenceEndMs: 8350}, start: 0, first: 1010, end: 7400, emitted: 128};
  expect(executorchMetrics(ok, 512, 128).executorchStats!.prefillChunks).toBe(2);
  expect(executorchMetrics(ok, 1024, 0).executorchStats!.prefillChunks).toBe(1);
  expect(() => executorchMetrics({...ok, first: -1}, 512, 0)).toThrow('no token');
  expect(() => executorchMetrics({...ok, stats: {...ok.stats, firstTokenMs: 1000}}, 512, 0)).toThrow('non-positive');
  expect(() => executorchMetrics({...ok, emitted: 127}, 512, 0)).toThrow('emitted 127');
  expect(() => executorchMetrics({...ok, stats: {...ok.stats, inferenceEndMs: NaN}}, 512, 0)).toThrow('non-finite');
});

test('the reference runner creates the adapter only where ExecuTorch is linked', () => {
  const {executorchAdapter} = require('../src/harness/reference');
  const options = {modelPath: '/files/m.pte', tokenizerPath: '/files/tokenizer.json', quantLabel: 'q', modelLabel: 'M',
    promptPrefix: '' as const};
  expect(executorchAdapter(options).engine).toBe('executorch');
  delete mockNative.ExecutorchBench;
  expect(() => executorchAdapter(options)).toThrow('not linked');
});
