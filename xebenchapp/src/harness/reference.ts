import * as FS from '@dr.pogodin/react-native-fs';
import {Platform} from 'react-native';
import bundledConfig from '../../configs/cpu-reference-v1.json';
import {parseConfig} from './config';
import {runSession, ReferenceSession, UNMEASURED_NOTES, unmeasuredSession} from './session';
import {LlamaRnAdapter} from './adapters/LlamaRnAdapter';
import {generateSeed, ORDER_METHOD, seededOrder} from './order';
import type {ReadinessBaseline} from './readiness';
import {stopMonitor} from './conditions';

let sequence = 0;

export async function runReference(log: (message: string) => void) {
  if (Platform.OS !== 'android') {
    throw new Error('The reference protocol currently supports Android');
  }
  const root = FS.ExternalDirectoryPath;
  const configFile = `${root}/xebench-config.json`;
  if (await FS.exists(configFile) && (await FS.stat(configFile)).size > 65536) {
    throw new Error('Configuration exceeds 64 KiB');
  }
  const config = parseConfig(await FS.exists(configFile) ? JSON.parse(await FS.readFile(configFile, 'utf8')) : bundledConfig);
  const directory = `${root}/xebench-results`;
  await FS.mkdir(directory);
  const seed = config.orderSeed ?? generateSeed();
  const runs = seededOrder(config.runs, seed);
  const order = runs.map(run => run.id);
  log(`Configuration ${config.id}: ${runs.length} CPU run(s); order seed ${seed}: ${order.join(', ')}`);
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
      nThreads: run.nThreads, nCtx: run.nCtx, nGpuLayers: 0});
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
    const identity = {runId, timestampIso: new Date().toISOString(), artifact: null,
      deviceInfo: {model: Platform.constants.Model, androidRelease: Platform.constants.Release}};
    // Runs that never reach measurement are still recorded, never silently dropped.
    const recordUnmeasured = async (stopReason: keyof typeof UNMEASURED_NOTES) => {
      await persist(unmeasuredSession(adapter, config, run, identity, runOrder, stopReason));
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
