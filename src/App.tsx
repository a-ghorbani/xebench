/**
 * xebench v0.1 — cross-engine on-device LLM benchmark harness.
 * One engine suite per app launch is the recommended usage (clean VmHWM /
 * process state); the UI warns when a second engine runs in the same process.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';

import { LlamaRnAdapter } from './harness/adapters/LlamaRnAdapter';
import { ExecutorchAdapter } from './harness/adapters/ExecutorchAdapter';
import type { EngineAdapter } from './harness/adapters/EngineAdapter';
import { runEngineSession, DEFAULT_PROTOCOL } from './harness/protocol';
import { probe } from './harness/probe';
import type { DeviceInfo } from './harness/types';

// ---- Model locations (pushed by scripts/fetch-models.sh) ----
// GGUF lives on app-external storage; .pte + tokenizer likewise.
const MODEL_DIR = '/storage/emulated/0/Android/data/com.xebench/files/models';

const GGUF_PATH = `${MODEL_DIR}/Llama-3.2-1B-Instruct-Q4_K_M.gguf`;
const PTE_PATH = `file://${MODEL_DIR}/llama_3_2_1b_xnnpack_spinquant.pte`;
const TOKENIZER_PATH = `file://${MODEL_DIR}/tokenizer.json`;
const TOKENIZER_CONFIG_PATH = `file://${MODEL_DIR}/tokenizer_config.json`;

const MODEL_LABEL = 'Llama 3.2 1B'; // canonical label per pocketpal-website matcher

function makeAdapter(engine: 'llama.cpp' | 'executorch'): EngineAdapter {
  if (engine === 'llama.cpp') {
    return new LlamaRnAdapter({
      modelPath: GGUF_PATH,
      quantLabel: 'Q4_K_M',
      modelLabel: MODEL_LABEL,
    });
  }
  return new ExecutorchAdapter({
    modelSource: PTE_PATH,
    tokenizerSource: TOKENIZER_PATH,
    tokenizerConfigSource: TOKENIZER_CONFIG_PATH,
    quantLabel: 'SpinQuant 4-bit',
    modelLabel: MODEL_LABEL,
  });
}

export default function App(): React.JSX.Element {
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [sustainedOn, setSustainedOn] = useState(true);
  const [lines, setLines] = useState<string[]>([]);
  const enginesRunThisProcess = useRef(0);
  const scrollRef = useRef<ScrollView>(null);

  useEffect(() => {
    probe.getDeviceInfo().then(setDevice).catch(console.error);
  }, []);

  const log = useCallback((msg: string) => {
    console.log(`[xebench] ${msg}`);
    setLines((prev) => [...prev.slice(-400), msg]);
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: false }), 50);
  }, []);

  const runSuite = useCallback(
    async (engine: 'llama.cpp' | 'executorch') => {
      if (busy) return;
      setBusy(true);
      const runId = `${engine.replace('.', '')}-${new Date()
        .toISOString()
        .replace(/[:.]/g, '-')
        .slice(0, 19)}`;
      try {
        const adapter = makeAdapter(engine);
        const rec = await runEngineSession(adapter, {
          ...DEFAULT_PROTOCOL,
          sustainedTargetSec: sustainedOn ? DEFAULT_PROTOCOL.sustainedTargetSec : 0,
          runId,
          processReused: enginesRunThisProcess.current > 0,
          log,
        });
        enginesRunThisProcess.current += 1;
        if (rec.guardsPassed) {
          log(
            `DONE ${engine}: pp=${rec.summary.prefillTps.median}t/s ` +
              `tg=${rec.summary.decodeTps.median}t/s ttft=${rec.summary.ttftMs.median}ms ` +
              `peak=${rec.summary.peakPssMb.median}MB (n=${rec.summary.decodeTps.n})`,
          );
          log('TIP: restart the app before running the other engine (clean process).');
        }
      } catch (e) {
        log(`ERROR: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setBusy(false);
      }
    },
    [busy, sustainedOn, log],
  );

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />
      <Text style={styles.title}>xebench v0.1</Text>
      <Text style={styles.subtitle}>
        {device
          ? `${device.manufacturer} ${device.model} · ${device.soc} · Android ${device.androidRelease} · ${device.cpuCores} cores · ${Math.round(device.totalMemMb / 1024)} GB`
          : 'reading device info...'}
      </Text>

      <View style={styles.row}>
        <Pressable
          style={[styles.btn, busy && styles.btnDisabled]}
          disabled={busy}
          onPress={() => runSuite('llama.cpp')}>
          <Text style={styles.btnText}>Run llama.rn suite{'\n'}(GGUF Q4_K_M)</Text>
        </Pressable>
        <Pressable
          style={[styles.btn, busy && styles.btnDisabled]}
          disabled={busy}
          onPress={() => runSuite('executorch')}>
          <Text style={styles.btnText}>Run ExecuTorch suite{'\n'}(SpinQuant .pte)</Text>
        </Pressable>
      </View>

      <View style={styles.switchRow}>
        <Text style={styles.switchLabel}>Include 5-min sustained phase</Text>
        <Switch value={sustainedOn} onValueChange={setSustainedOn} disabled={busy} />
      </View>

      <ScrollView ref={scrollRef} style={styles.log}>
        {lines.map((l, i) => (
          <Text key={i} style={styles.logLine}>
            {l}
          </Text>
        ))}
      </ScrollView>

      <Text style={styles.footer}>
        results: adb pull /sdcard/Android/data/com.xebench/files/results/
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#111318', padding: 16 },
  title: { color: '#fff', fontSize: 22, fontWeight: '700', marginTop: 8 },
  subtitle: { color: '#9aa0aa', fontSize: 12, marginTop: 4, marginBottom: 16 },
  row: { flexDirection: 'row', gap: 12 },
  btn: {
    flex: 1,
    backgroundColor: '#2f6fed',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  btnDisabled: { opacity: 0.4 },
  btnText: { color: '#fff', fontWeight: '600', textAlign: 'center', fontSize: 13 },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginVertical: 12,
  },
  switchLabel: { color: '#c8cdd6', fontSize: 13 },
  log: {
    flex: 1,
    backgroundColor: '#0a0c10',
    borderRadius: 8,
    padding: 10,
  },
  logLine: { color: '#7ee787', fontFamily: 'monospace', fontSize: 11, lineHeight: 16 },
  footer: { color: '#5b616c', fontSize: 10, marginTop: 8, fontFamily: 'monospace' },
});
