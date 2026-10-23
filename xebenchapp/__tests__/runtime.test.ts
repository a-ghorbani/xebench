import {llamaCppBuild, version} from '../src/harness/runtime';
import {liteRtRuntimeVersions} from '../src/harness/adapters/LiteRtAdapter';

declare const require: ((name: string) => any) & {resolve: (name: string) => string};
const fs = require('fs');

test('llama.cpp build comes from llama.rn BuildInfo and is strict', () => {
  expect(llamaCppBuild({number: '11385', commit: 'bf9a0cc'})).toEqual({buildNumber: 11385, commit: 'bf9a0cc'});
  expect(llamaCppBuild({number: 11385, commit: 'bf9a0cc1234567890abcdef1234567890abcdef1'})).toEqual(
    {buildNumber: 11385, commit: 'bf9a0cc1234567890abcdef1234567890abcdef1'});
  for (const bad of [null, {}, {number: 'b11385', commit: 'bf9a0cc'}, {number: '11385', commit: 'BF9A0CC'},
    {number: '11385', commit: 'bf9a0'}, {number: '0', commit: 'bf9a0cc'}, {number: '1.5', commit: 'bf9a0cc'}]) {
    expect(llamaCppBuild(bad)).toBeNull();
  }
});

test('the installed llama.rn exports a well-formed llama.cpp build', () => {
  // index.ts exports BuildInfo = {number: BUILD_NUMBER, commit: BUILD_COMMIT} from version.ts.
  const text: string = fs.readFileSync(require.resolve('llama.rn/src/version.ts'), 'utf8');
  const number = /BUILD_NUMBER = '([^']*)'/.exec(text)?.[1];
  const commit = /BUILD_COMMIT = '([^']*)'/.exec(text)?.[1];
  expect(llamaCppBuild({number, commit})).not.toBeNull();
});

test('versions are short strings, never free text', () => {
  for (const ok of ['0.18.0', '2.3.0', '0.13.0-rc.7', '2.50.0.260828221209', 'b11385']) {
    expect(version(ok)).toBe(ok);
  }
  for (const bad of ['', ' 0.18.0', '0.18.0 (npu)', 'v0.18.0, beta', 'x'.repeat(41), 18, null]) {
    expect(version(bad)).toBeNull();
  }
});

test('LiteRT-LM records the Qualcomm runtime and QAIRT only for NPU runs', () => {
  const constants = {litertLmVersion: '0.18.0', litertNpuRuntimeQualcommVersion: '2.3.0',
    qairtVersion: '2.50.0.260828221209'};
  expect(liteRtRuntimeVersions(constants, 'npu')).toEqual({llamaCpp: null, llamaRn: null, litertLm: '0.18.0',
    litertNpuRuntimeQualcomm: '2.3.0', qairt: '2.50.0.260828221209', genie: null, executorch: null,
    reactNativeExecutorch: null});
  expect(liteRtRuntimeVersions(constants, 'gpu')).toMatchObject({litertLm: '0.18.0', litertNpuRuntimeQualcomm: null,
    qairt: null});
  // Unpackaged libraries (the native module reports null) stay null.
  expect(liteRtRuntimeVersions({litertLmVersion: null, qairtVersion: null}, 'npu')).toMatchObject(
    {litertLm: null, litertNpuRuntimeQualcomm: null, qairt: null});
});
