import * as FS from '@dr.pogodin/react-native-fs';
import {NativeModules, Platform} from 'react-native';
import bundledConfig from '../../configs/cpu-reference-v2.json';
import {AcceleratorRun, parseAcceleratorConfig, parseConfig} from './config';
import {runSession, UNMEASURED_NOTES, unmeasuredSession} from './session';
import type {ConfigurationSource, ReferenceSession, SessionIdentity} from './session';
import {LlamaRnAdapter} from './adapters/LlamaRnAdapter';
import {generateSeed, ORDER_METHOD, seededOrder} from './order';
import type {ReadinessBaseline} from './readiness';
import {stopMonitor} from './conditions';
import {readBuildInfo} from './build';
import type {BuildIdentity} from './build';
import {AcceleratorSession, runAcceleratorConfiguration} from './accelerator';
import {LiteRtAdapter} from './adapters/LiteRtAdapter';
import type {EngineAdapter} from './adapters/EngineAdapter';
import type {ExecutorchAdapterOptions} from './adapters/ExecutorchAdapter';

/**
 * ExecuTorch runs (experimental flavor only). react-native-executorch installs its JSI library
 * when it is imported, and other flavors do not package that library, so the adapter module is
 * required only after the flavor's ExecutorchBench module is found; otherwise the run is
 * recorded as engine-unavailable.
 */
export function executorchAdapter(options: ExecutorchAdapterOptions): EngineAdapter {
  if (!NativeModules.ExecutorchBench) {
    throw new Error('ExecuTorch is not linked in this build (experimental flavor only)');
  }
  const {ExecutorchAdapter} = require('./adapters/ExecutorchAdapter') as typeof import('./adapters/ExecutorchAdapter');
  return new ExecutorchAdapter(options);
}

let sequence = 0;

export async function runReference(log: (message: string) => void) {
  if (Platform.OS !== 'android') {
    throw new Error('The reference protocol currently supports Android');
  }
  const root = FS.ExternalDirectoryPath;
  const configFile = `${root}/xebench-config.json`;
  const buildInfo = await readBuildInfo();
  const staged = await FS.exists(configFile);
  let input: unknown = bundledConfig;
  // A debug build loads its JS, including the bundled configuration, from Metro: the
  // build-time hash would describe a file the running code may not have used.
  let configurationSource: ConfigurationSource = {kind: 'bundled',
    sha256: buildInfo.identity.buildType === 'release' ? buildInfo.bundledConfigSha256 : null};
  if (staged) {
    if ((await FS.stat(configFile)).size > 65536) {
      throw new Error('Configuration exceeds 64 KiB');
    }
    // Hash, read, hash again: the recorded hash is of exactly the bytes that were parsed.
    const sha256 = await FS.hash(configFile, 'sha256');
    const text = await FS.readFile(configFile, 'utf8');
    if (await FS.hash(configFile, 'sha256') !== sha256) {
      throw new Error('Configuration changed while it was read');
    }
    input = JSON.parse(text);
    configurationSource = {kind: 'staged', sha256};
  }
  const directory = `${root}/xebench-results`;
  if ((input as {schemaVersion?: unknown} | null)?.schemaVersion === 2) {
    return runAccelerators(input, root, directory, log,
      {model: Platform.constants.Model, androidRelease: Platform.constants.Release},
      buildInfo.identity, configurationSource);
  }
  const config = parseConfig(input);
  await FS.mkdir(directory);
  const seed = config.orderSeed ?? generateSeed();
  const runs = seededOrder(config.runs, seed);
  const order = runs.map(run => run.id);
  log(`Configuration ${config.id} (${configurationSource.kind}): ${runs.length} CPU run(s); ` +
    `order seed ${seed}: ${order.join(', ')}`);
  const build = buildInfo.identity;
  log(`Build ${build.flavor ?? 'unknown'} ${build.gitCommit?.slice(0, 12) ?? 'unknown'}` +
    (build.gitDirty ? ' (dirty)' : ''));
  // Reclaim a monitor orphaned by an earlier invocation (e.g. a JS reload); a stuck one
  // stays registered and makes this configuration abort with recorded evidence.
  const leftover = await stopMonitor();
  if (leftover.status !== 'not-running') log(`Condition monitor before start: ${leftover.status}`);
  const baseline: ReadinessBaseline = {batteryTempC: null};
  let fatal: Error | null = null;
  for (const [position, run] of runs.entries()) {
    const modelPath = `${root}/${run.modelFile}`;
    const runId = `${Date.now()}-${sequence++}-${Math.random().toString(36).slice(2)}`;
    const adapter = new LlamaRnAdapter({modelPath, modelLabel: run.model, quantLabel: run.quant,
      nThreads: run.nThreads, nCtx: run.nCtx, nGpuLayers: 0,
      // Unset load settings (undefined) keep the adapter defaults.
      useMmap: run.useMmap, useMlock: run.useMlock, noExtraBufts: run.noExtraBufts, flashAttnType: run.flashAttnType});
    const runOrder = {method: ORDER_METHOD, seed, seedSource: config.orderSeed === undefined ? 'generated' as const :
      'configured' as const, order, position};
    const persist = async (record: ReferenceSession) => {
      const name = `${runId}-${record.coldRuns.length}.json`;
      const path = `${directory}/${name}`;
      if (await FS.exists(path)) {
        throw new Error('Session file already exists');
      }
      await FS.writeFile(path, JSON.stringify(record), 'utf8');
      const sha256 = await FS.hash(path, 'sha256');
      // Full records exceed logcat's line limit. Emit only a bounded reference.
      console.log('XEBENCH_RESULT_FILE ' + JSON.stringify({name, sha256}));
      log(`${run.id}: ${record.coldRuns.length}/${config.nColdRuns} ${record.status}` +
        (record.stopReason ? ` (${record.stopReason})` : ''));
    };
    const identity: SessionIdentity = {runId, timestampIso: new Date().toISOString(), artifact: null,
      deviceInfo: {model: Platform.constants.Model, androidRelease: Platform.constants.Release},
      build, configurationSource};
    // Runs that never reach measurement are still recorded, never silently dropped.
    const recordUnmeasured = async (stopReason: keyof typeof UNMEASURED_NOTES,
      artifact: SessionIdentity['artifact'] = null) => {
      await persist(unmeasuredSession(adapter, config, run, {...identity, artifact}, runOrder, stopReason));
      console.log(`XEBENCH_ERROR ${run.id}: ${stopReason}`);
    };
    if (fatal) {
      try {
        // A constant note: engine error text stays in the earlier session's error field.
        await recordUnmeasured('configuration-aborted');
      } catch {
        break; // Persistence itself is failing; keep the original fatal error.
      }
      continue;
    }
    let artifact: {sha256: string; sizeBytes: number};
    try {
      log(`${run.id}: hashing model before measurement…`);
      artifact = {sha256: await FS.hash(modelPath, 'sha256'), sizeBytes: (await FS.stat(modelPath)).size};
    } catch {
      try {
        await recordUnmeasured('setup-failed');
      } catch (failure) {
        fatal = failure instanceof Error ? failure : new Error(String(failure));
      }
      continue;
    }
    if (run.modelSha256 !== undefined && artifact.sha256 !== run.modelSha256) {
      // The wrong bytes are staged: record both hashes, measure nothing, abort the rest.
      fatal = new Error(`${run.id}: model SHA-256 does not match the configuration`);
      try {
        await recordUnmeasured('model-hash-mismatch', artifact);
      } catch (failure) {
        fatal = failure instanceof Error ? failure : new Error(String(failure));
      }
      continue;
    }
    try {
      const result = await runSession(adapter, config, run, {...identity, artifact}, persist, undefined, {
        runOrder, baseline,
      });
      if (result.status === 'failed') {
        console.log(`XEBENCH_ERROR ${run.id}: see session evidence`);
      }
    } catch (failure) {
      // Cleanup, probe or persistence failures make later native work unsafe or unrecordable.
      fatal = failure instanceof Error ? failure : new Error(String(failure));
    }
  }
  if (fatal) {
    throw fatal;
  }
}

/** Evidence file per checkpoint; only a bounded reference goes to logcat. */
function persister(directory: string, runId: string, label: string, planned: number, log: (message: string) => void) {
  return async (record: {coldRuns: unknown[]; status: string; stopReason: string | null}) => {
    const name = `${runId}-${record.coldRuns.length}.json`;
    const path = `${directory}/${name}`;
    if (await FS.exists(path)) {
      throw new Error('Session file already exists');
    }
    await FS.writeFile(path, JSON.stringify(record), 'utf8');
    const sha256 = await FS.hash(path, 'sha256');
    console.log('XEBENCH_RESULT_FILE ' + JSON.stringify({name, sha256}));
    log(`${label}: ${record.coldRuns.length}/${planned} ${record.status}` +
      (record.stopReason ? ` (${record.stopReason})` : ''));
  };
}

/** accelerator-v2 configuration (schemaVersion 2): llama.cpp and LiteRT-LM CPU/GPU/NPU, ExecuTorch CPU. */
async function runAccelerators(raw: unknown, root: string, directory: string, log: (message: string) => void,
  deviceInfo: {model: string; androidRelease: string}, build: BuildIdentity,
  configurationSource: ConfigurationSource) {
  const config = parseAcceleratorConfig(raw);
  await FS.mkdir(directory);
  const seed = config.orderSeed ?? generateSeed();
  const runs = seededOrder(config.runs, seed);
  const order = runs.map(run => run.id);
  log(`Configuration ${config.id} (${configurationSource.kind}): ${runs.length} run(s); ` +
    `order seed ${seed}: ${order.join(', ')}`);
  log(`Build ${build.flavor ?? 'unknown'} ${build.gitCommit?.slice(0, 12) ?? 'unknown'}` +
    (build.gitDirty ? ' (dirty)' : ''));
  const leftover = await stopMonitor();
  if (leftover.status !== 'not-running') log(`Condition monitor before start: ${leftover.status}`);
  const modelPath = (run: AcceleratorRun) => `${root}/${run.modelFile}`;
  await runAcceleratorConfiguration(config, runs, (_run, position) => ({method: ORDER_METHOD, seed,
    seedSource: config.orderSeed === undefined ? 'generated' : 'configured', order, position}), {
    artifact: async run => ({sha256: await FS.hash(modelPath(run), 'sha256'), sizeBytes: (await FS.stat(modelPath(run))).size}),
    tokenizer: async run => {
      const path = `${root}/${run.tokenizerFile}`;
      return {sha256: await FS.hash(path, 'sha256'), sizeBytes: (await FS.stat(path)).size};
    },
    adapter: run => run.engine === 'executorch' ?
      executorchAdapter({modelPath: modelPath(run), tokenizerPath: `${root}/${run.tokenizerFile}`,
        modelLabel: run.model, quantLabel: run.quant}) : run.engine === 'llama.cpp' ?
      new LlamaRnAdapter({modelPath: modelPath(run), modelLabel: run.model, quantLabel: run.quant,
        nThreads: run.nThreads!, nCtx: run.nCtx!, nGpuLayers: run.nGpuLayers ?? 0,
        ...(run.devices ? {devices: run.devices} : {}),
        // Unset load settings (undefined) keep the adapter defaults.
        useMmap: run.useMmap, useMlock: run.useMlock, noExtraBufts: run.noExtraBufts, flashAttnType: run.flashAttnType}) :
      new LiteRtAdapter({modelPath: modelPath(run), backend: run.backend, modelLabel: run.model,
        quantLabel: run.quant, nThreads: run.nThreads}),
    identity: () => ({runId: `${Date.now()}-${sequence++}-${Math.random().toString(36).slice(2)}`,
      timestampIso: new Date().toISOString(), artifact: null, deviceInfo, build, configurationSource}),
    persister: (run, identity) => persister(directory, identity.runId, run.id, config.nColdRuns, log) as
      (record: AcceleratorSession) => Promise<void>,
    log,
  });
}
