import {parseAcceleratorConfig, parseConfig} from '../src/harness/config';
import {ACCELERATOR_PROMPT, chatTurn, STANDARD_PROMPT} from '../src/harness/prompts';
import f8Gemma from '../configs/f8-gemma3-accelerators-v1.json';
import bundled from '../configs/cpu-reference-v2.json';
import v1 from '../configs/cpu-reference-v1.json';
import experiment from '../configs/experiment-charging-ab-v1.json';
import v3 from '../configs/cpu-reference-v3.json';
import qwen from '../configs/cpu-reference-v3-qwen25.json';
import gemma from '../configs/cpu-reference-v3-gemma3.json';
import qwenOpencl from '../configs/llamacpp-qwen25-opencl-v1.json';
import q4kmHexagon from '../configs/llamacpp-llama32-q4km-hexagon-v1.json';
import litertGemmaCpu from '../configs/litertlm-gemma3-int4-cpu-v1.json';
import litertQwenCpu from '../configs/litertlm-qwen25-q8-cpu-v1.json';
import litertGemmaNpu from '../configs/litertlm-gemma3-sm8850-npu-v1.json';
import litertGemmaCpuFixed from '../configs/litertlm-gemma3-int4-cpu-v2.json';
import litertQwenCpuFixed from '../configs/litertlm-qwen25-q8-cpu-v2.json';
import litertGemmaNpuFixed from '../configs/litertlm-gemma3-sm8850-npu-v2.json';

test('bundled CPU reference configuration is valid', () => {
  expect(parseConfig(bundled)).toEqual(bundled);
});

test('cpu-reference-v1 stays valid and sets no load settings', () => {
  expect(parseConfig(v1)).toEqual(v1);
  for (const key of ['useMmap', 'useMlock', 'noExtraBufts', 'flashAttnType']) {
    expect(parseConfig(v1).runs[0]).not.toHaveProperty(key);
  }
});

test('cpu-reference-v2 is cpu-reference-v1 with PocketPal\'s Android load defaults', () => {
  const {useMmap, useMlock, noExtraBufts, flashAttnType, ...run} = bundled.runs[0];
  expect({useMmap, useMlock, noExtraBufts, flashAttnType})
    .toEqual({useMmap: false, useMlock: false, noExtraBufts: false, flashAttnType: 'off'});
  expect({...bundled, id: v1.id, runs: [run]}).toEqual(v1);
});

test('cpu-reference-v3 runs Q4_K_M, Q4_0 and Q8_0 with v2 settings except flash attention auto and longer cooling', () => {
  expect(parseConfig(v3)).toEqual(v3);
  expect(v3.runs.map(run => [run.quant, run.modelFile])).toEqual([
    ['Q4_K_M', 'Llama-3.2-1B-Instruct-Q4_K_M.gguf'], ['Q4_0', 'Llama-3.2-1B-Instruct-Q4_0.gguf'],
    ['Q8_0', 'Llama-3.2-1B-Instruct-Q8_0.gguf']]);
  const {runs, id, ...rest} = v3;
  const {runs: v2Runs, id: v2Id, ...v2Rest} = bundled;
  expect([id, v2Id]).toEqual(['cpu-reference-v3', 'cpu-reference-v2']);
  // Nine repetitions share one battery-temperature baseline: a longer cooldown and readiness
  // wait let the phone cool back within the 2 °C bound instead of skipping a repetition.
  expect(rest).toEqual({...v2Rest, cooldownSec: 120, readinessTimeoutSec: 900});
  expect(rest.maxBatteryTempRiseC).toBe(2);
  const identity = ['id', 'modelFile', 'quant', 'modelSha256'];
  const others = (run: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(run).filter(([key]) => !identity.includes(key)));
  for (const run of runs) {
    expect(run.id).toMatch(/^llama32-q[0-9a-z]+-cpu$/);
    expect(run.modelSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(run.modelFile.endsWith(`-${run.quant}.gguf`)).toBe(true);
    expect(others(run)).toEqual({...others(v2Runs[0]), flashAttnType: 'auto'});
  }
});

test.each([
  {useMmap: 'false'}, {useMmap: 0}, {useMmap: null}, {useMlock: 'true'}, {noExtraBufts: 1},
  {flashAttnType: 'enabled'}, {flashAttnType: true}, {flashAttnType: null}, {flash_attn_type: 'off'},
])('rejects invalid load setting %j', change => {
  expect(() => parseConfig({...bundled, runs: [{...bundled.runs[0], ...change}]})).toThrow();
});

test('each load setting is kept exactly when set', () => {
  for (const change of [{useMmap: true}, {useMlock: true}, {noExtraBufts: true}, {flashAttnType: 'auto'},
    {flashAttnType: 'on'}]) {
    expect(parseConfig({...v1, runs: [{...v1.runs[0], ...change}]}).runs[0]).toEqual({...v1.runs[0], ...change});
  }
});

test.each([
  {schemaVersion: 2}, {nColdRuns: 0}, {nColdRuns: 2.5}, {cooldownSec: -1},
  {maxDecodeTokens: 0}, {runs: []}, {nGpuLayers: 99}, {readinessTimeoutSec: 5},
  {readinessTimeoutSec: 901}, {maxBatteryTempRiseC: 0.1}, {maxBatteryTempRiseC: 11}, {maxBatteryTempRiseC: '2'},
  {orderSeed: -1}, {orderSeed: 2 ** 32}, {orderSeed: 1.5},
  {experimentChargingReadiness: 'skip'}, {experimentChargingReadiness: true},
])('rejects invalid configuration %j', change => {
  expect(() => parseConfig({...bundled, ...change})).toThrow();
});

test.each([
  {engine: 'litert-lm'}, {modelFile: '../model.gguf'}, {modelFile: '/tmp/model.gguf'},
  {modelFile: 'https://example.test/model.gguf'}, {nThreads: 0}, {nCtx: 512}, {backend: 'gpu'},
  {modelSha256: 'A'.repeat(64)}, {modelSha256: 'a'.repeat(63)}, {modelSha256: null}, {modelSha256: 1},
])('rejects unsafe or unsupported run %j', change => {
  expect(() => parseConfig({...bundled, runs: [{...bundled.runs[0], ...change}]})).toThrow();
});

test('rejects duplicate run IDs', () => {
  expect(() => parseConfig({...bundled, runs: [bundled.runs[0], bundled.runs[0]]})).toThrow();
});

test('readiness timeout defaults to the protocol bound and a configured order seed is kept', () => {
  const legacy: Record<string, unknown> = {...bundled};
  delete legacy.readinessTimeoutSec;
  delete legacy.maxBatteryTempRiseC;
  expect(parseConfig(legacy).readinessTimeoutSec).toBe(180);
  expect(parseConfig(legacy).maxBatteryTempRiseC).toBe(2);
  expect(parseConfig({...bundled, maxBatteryTempRiseC: 3.5}).maxBatteryTempRiseC).toBe(3.5);
  expect(parseConfig(legacy)).not.toHaveProperty('orderSeed');
  expect(parseConfig({...bundled, orderSeed: 0}).orderSeed).toBe(0);
});

test('an expected model SHA-256 is optional and kept exactly', () => {
  const sha = '0123456789abcdef'.repeat(4);
  expect(parseConfig({...bundled, runs: [{...bundled.runs[0], modelSha256: sha}]}).runs[0].modelSha256).toBe(sha);
  const {modelSha256, ...unpinned} = bundled.runs[0];
  expect(modelSha256).toMatch(/^[0-9a-f]{64}$/);
  expect(parseConfig({...bundled, runs: [unpinned]}).runs[0]).not.toHaveProperty('modelSha256');
});

test('the experiment-only charging readiness flag is kept only when set', () => {
  expect(parseConfig(experiment)).toEqual(experiment);
  expect(parseConfig(bundled)).not.toHaveProperty('experimentChargingReadiness');
  expect(parseConfig({...bundled, experimentChargingReadiness: 'record-only'}).experimentChargingReadiness)
    .toBe('record-only');
});

test('the board configurations for Qwen2.5 and Gemma 3 parse to their files with v3 settings', () => {
  for (const config of [qwen, gemma]) {
    expect(parseConfig(config)).toEqual(config);
    expect(config.runs[0]).toMatchObject({useMmap: false, useMlock: false, noExtraBufts: false, flashAttnType: 'auto'});
  }
  for (const config of [qwenOpencl, q4kmHexagon]) {
    expect(parseAcceleratorConfig(config)).toEqual(config);
    for (const run of config.runs) {
      expect(run).toMatchObject({useMmap: false, useMlock: false, noExtraBufts: false, flashAttnType: 'auto'});
    }
  }
  expect(qwenOpencl.chatTemplate).toBe('qwen25');
  expect([qwenOpencl.measuredPrompt, q4kmHexagon.measuredPrompt]).toEqual(['std-pp512-v1', 'std-pp512-v1']);
});

test('accelerator load settings apply to llama.cpp runs only and are validated', () => {
  const base = parseAcceleratorConfig(qwenOpencl);
  expect(base.runs[1].useMmap).toBe(false);
  const {useMmap, useMlock, noExtraBufts, flashAttnType, ...plain} = qwenOpencl.runs[1];
  expect([useMmap, useMlock, noExtraBufts, flashAttnType]).toEqual([false, false, false, 'auto']);
  expect(parseAcceleratorConfig({...qwenOpencl, runs: [qwenOpencl.runs[0], plain]}).runs[1]).not.toHaveProperty('useMmap');
  for (const change of [{useMmap: 'false'}, {flashAttnType: 'enabled'}, {noExtraBufts: 0}]) {
    expect(() => parseAcceleratorConfig({...qwenOpencl, runs: [qwenOpencl.runs[0], {...qwenOpencl.runs[1], ...change}]}))
      .toThrow();
  }
});

test('LiteRT-LM runs refuse llama.cpp load settings', () => {
  const litert = f8Gemma.runs.findIndex(run => run.engine === 'litert-lm');
  for (const change of [{useMmap: false}, {flashAttnType: 'auto'}]) {
    const runs = f8Gemma.runs.map((run, index) => (index === litert ? {...run, ...change} : run));
    expect(() => parseAcceleratorConfig({...f8Gemma, runs})).toThrow('apply to llama.cpp only');
  }
});

test('the qwen25 chat template is a ChatML user turn without a system message', () => {
  expect(chatTurn('qwen25', 'Hi')).toBe('<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n');
  expect(chatTurn('llama32', 'Hi')).toBe(
    '<|start_header_id|>user<|end_header_id|>\n\nHi<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n');
  expect(chatTurn('gemma3', 'Hi')).toBe('<start_of_turn>user\nHi<end_of_turn>\n<start_of_turn>model\n');
});

test('measuredPrompt is optional, validated, and std-pp512-v1 applies to both engines', () => {
  const {measuredPrompt, ...plain} = qwenOpencl;
  expect(measuredPrompt).toBe('std-pp512-v1');
  expect(parseAcceleratorConfig(plain)).not.toHaveProperty('measuredPrompt');
  expect(parseAcceleratorConfig({...plain, measuredPrompt: 'std-chat-v1'}).measuredPrompt).toBe('std-chat-v1');
  expect(() => parseAcceleratorConfig({...plain, measuredPrompt: 'std-pp1024'})).toThrow('measuredPrompt');
  expect(parseAcceleratorConfig({...f8Gemma, measuredPrompt: 'std-pp512-v1'}).measuredPrompt).toBe('std-pp512-v1');
});

test('the LiteRT-LM board configurations measure std-pp512-v1 under the cpu-reference-v3 timing', () => {
  for (const config of [litertGemmaCpu, litertQwenCpu, litertGemmaNpu].map(parseAcceleratorConfig)) {
    expect(config).toMatchObject({nColdRuns: 3, cooldownSec: 120, readinessTimeoutSec: 900, maxBatteryTempRiseC: 2,
      maxDecodeTokens: 128, measuredPrompt: 'std-pp512-v1'});
    for (const run of config.runs) {
      expect(run).toMatchObject({engine: 'litert-lm', purpose: 'measurement', nCtx: null,
        nThreads: run.backend === 'cpu' ? 4 : null});
      expect(run.modelSha256).toMatch(/^[0-9a-f]{64}$/);
    }
  }
  expect(parseAcceleratorConfig(litertGemmaNpu).runs.map(run => `${run.backend}:${run.referenceRunId}`))
    .toEqual(['cpu:null', 'npu:gemma3-1b-int4-litertlm-cpu']);
});

test('the measured prompts have the SHA-256 values the host validator pins', () => {
  const {createHash} = require('crypto');
  const digest = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex');
  expect(digest(STANDARD_PROMPT)).toBe('d4af02ae1c169637720a824ed879956385446ffd8823e287b30c8b11756c90f4');
  expect(digest(ACCELERATOR_PROMPT)).toBe('97b05f26c77fa84504cfa238dad74591d882e5e0a6264a47f05dd03605bdb4f8');
});

test('the v2 LiteRT-LM configurations are v1 with decodeMode fixed', () => {
  for (const [first, v2] of [[litertGemmaCpu, litertGemmaCpuFixed], [litertQwenCpu, litertQwenCpuFixed],
    [litertGemmaNpu, litertGemmaNpuFixed]] as const) {
    const {decodeMode, id, ...rest} = parseAcceleratorConfig(v2);
    expect(decodeMode).toBe('fixed');
    expect(id).toBe(first.id.replace(/-v1$/, '-v2'));
    expect({...rest, id: first.id}).toEqual(parseAcceleratorConfig(first));
    expect(parseAcceleratorConfig(v2)).toEqual(v2);
  }
});

test('decodeMode is optional, validated, and fixed needs LiteRT-LM runs on the default thread count', () => {
  expect(parseAcceleratorConfig(litertGemmaCpu)).not.toHaveProperty('decodeMode');
  expect(() => parseAcceleratorConfig({...litertGemmaCpu, decodeMode: 'until-128'})).toThrow('decodeMode');
  expect(() => parseAcceleratorConfig({...qwenOpencl, decodeMode: 'fixed'})).toThrow('LiteRT-LM runs only');
  const eight = {...litertGemmaCpu, runs: [{...litertGemmaCpu.runs[0], nThreads: 8}]};
  expect(() => parseAcceleratorConfig({...eight, decodeMode: 'fixed'})).toThrow('nThreads 4');
});
