import * as FS from '@dr.pogodin/react-native-fs';
import {Platform} from 'react-native';
import bundledConfig from '../../configs/cpu-reference-v1.json';
import {parseConfig} from './config';
import {runSession, ReferenceSession} from './session';
import {LlamaRnAdapter} from './adapters/LlamaRnAdapter';

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
  log(`Configuration ${config.id}: ${config.runs.length} CPU run(s)`);
  for (const run of config.runs) {
    const modelPath = `${root}/${run.modelFile}`;
    log(`${run.id}: hashing model before measurement…`);
    const artifact = {sha256: await FS.hash(modelPath, 'sha256'), sizeBytes: (await FS.stat(modelPath)).size};
    const runId = `${Date.now()}-${sequence++}-${Math.random().toString(36).slice(2)}`;
    const adapter = new LlamaRnAdapter({modelPath, modelLabel: run.model, quantLabel: run.quant,
      nThreads: run.nThreads, nCtx: run.nCtx, nGpuLayers: 0});
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
      log(`${run.id}: ${record.coldRuns.length}/${config.nColdRuns} ${record.status}`);
    };
    const result = await runSession(adapter, config, run, {
      runId, timestampIso: new Date().toISOString(), artifact,
      deviceInfo: {model: Platform.constants.Model, androidRelease: Platform.constants.Release},
    }, persist);
    if (result.status === 'failed') {
      console.log(`XEBENCH_ERROR ${run.id}: see session evidence`);
    }
  }
}
