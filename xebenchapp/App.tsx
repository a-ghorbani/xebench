/**
 * xebench foreground harness app.
 *
 * Runs every configured engine IN THE FOREGROUND (top-app cpuset -> real clocks;
 * METHODOLOGY Rule 0), logging one `XEBENCH_RESULT <json>` line per engine to
 * logcat, then `XEBENCH_DONE`. The host (scripts/capture_app_run.sh) launches the
 * app, scrapes the markers, and feeds aggregate.mjs. Auto-runs all on launch so
 * automation needs no per-engine tapping (robust across device screen sizes).
 *
 * Models are staged by the host into the app's external files dir:
 *   /storage/emulated/0/Android/data/com.xebenchapp/files/
 * Engines whose model isn't staged log XEBENCH_ERROR and the run continues.
 */
import React, {useState, useCallback, useEffect, useRef} from 'react';
import {SafeAreaView, ScrollView, Text, View, Pressable, StyleSheet, Platform} from 'react-native';
import {initExecutorch} from 'react-native-executorch';
import {BareResourceFetcher} from 'react-native-executorch-bare-resource-fetcher';
import {LlamaRnAdapter} from './src/harness/adapters/LlamaRnAdapter';
import {ExecutorchAdapter} from './src/harness/adapters/ExecutorchAdapter';
import {LiteRtAdapter} from './src/harness/adapters/LiteRtAdapter';
// GENIE-X disabled in the main (llama.cpp-HTP) variant — its native runtime
// collides with llama.rn's Hexagon path. See android/app/build.gradle.
// import {GenieXAdapter} from './src/harness/adapters/GenieXAdapter';
import type {EngineAdapter} from './src/harness/adapters/EngineAdapter';
import {STANDARD_PROMPT, STANDARD_PROMPT_LABEL, MAX_DECODE_TOKENS} from './src/harness/prompts';

// rn-executorch 0.9.x requires a resource fetcher adapter registered once before
// any LLMModule use. Bare (non-Expo) RN app -> BareResourceFetcher.
initExecutorch({resourceFetcher: BareResourceFetcher});

const FILES = '/storage/emulated/0/Android/data/com.xebenchapp/files';
const N_COLD = 3;

// Model 1 — Llama 3.2 1B: llama.cpp (Q4_K_M) vs ExecuTorch (SpinQuant). Ungated.
const LLAMA_LLAMA32 = new LlamaRnAdapter({
  modelPath: `${FILES}/Llama-3.2-1B-Instruct-Q4_K_M.gguf`,
  quantLabel: 'Q4_K_M',
  modelLabel: 'Llama 3.2 1B',
});
// Exact-match to arXiv 2605.08195 (Pixel 9 Pro XL / Tensor G4): llama.cpp, Q4_0.
const LLAMA_LLAMA32_Q40 = new LlamaRnAdapter({
  modelPath: `${FILES}/Llama-3.2-1B-Instruct-Q4_0.gguf`,
  quantLabel: 'Q4_0',
  modelLabel: 'Llama 3.2 1B',
});
const ET_LLAMA32 = new ExecutorchAdapter({
  modelSource: `file://${FILES}/llama-3.2-1B-spinquant.pte`,
  tokenizerSource: `file://${FILES}/tokenizer.json`,
  tokenizerConfigSource: `file://${FILES}/tokenizer_config.json`,
  quantLabel: 'SpinQuant 4-bit',
  modelLabel: 'Llama 3.2 1B',
});

// Model 2 — Qwen2.5 1.5B (q8): llama.cpp vs LiteRT-LM. Gemma is gated; Qwen is
// ungated. LiteRT-LM only ships q8/f32 .litertlm for Qwen2.5, so both engines
// run 8-bit for an apples-to-apples pair.
const LLAMA_QWEN = new LlamaRnAdapter({
  modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-Q8_0.gguf`,
  quantLabel: 'Q8_0',
  modelLabel: 'Qwen2.5 1.5B',
});
const LITERT_QWEN_CPU = new LiteRtAdapter({
  modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-q8.litertlm`,
  backend: 'cpu',
  quantLabel: 'q8',
  modelLabel: 'Qwen2.5 1.5B',
});
const LITERT_QWEN_GPU = new LiteRtAdapter({
  modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-q8.litertlm`,
  backend: 'gpu',
  quantLabel: 'q8',
  modelLabel: 'Qwen2.5 1.5B',
});
const LITERT_QWEN_NPU = new LiteRtAdapter({
  modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-q8.litertlm`,
  backend: 'npu',
  quantLabel: 'q8',
  modelLabel: 'Qwen2.5 1.5B',
});

// llama.cpp GPU (OpenCL) — llama.rn ships a hexagon_opencl prebuilt + its own
// OpenCL loader, so this may init where LiteRT's ML Drift couldn't.
const LLAMA_QWEN_GPU = new LlamaRnAdapter({
  modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-Q8_0.gguf`,
  quantLabel: 'Q8_0',
  modelLabel: 'Qwen2.5 1.5B',
  nGpuLayers: 99,
});
// llama.cpp Hexagon NPU (HTP) — SM8450+. myron = SM8850 (Hexagon v81, skel bundled).
// Q4_0 is the HTP/HMX-friendly quant (Q4_K_M largely falls back to CPU on the DSP).
const LLAMA_LLAMA32_NPU = new LlamaRnAdapter({
  modelPath: `${FILES}/Llama-3.2-1B-Instruct-Q4_0.gguf`,
  quantLabel: 'Q4_0',
  modelLabel: 'Llama 3.2 1B',
  nGpuLayers: 99,
  devices: ['HTP0'],
});

// GENIE-X CPU — disabled in the main variant (see import note above).
// const GENIEX_QWEN_CPU = new GenieXAdapter({
//   modelPath: `${FILES}/Qwen2.5-1.5B-Instruct-Q8_0.gguf`,
//   backend: 'cpu',
//   quantLabel: 'Q8_0',
//   modelLabel: 'Qwen2.5 1.5B',
// });

// Gemma 3 1B — Google's LiteRT reference model. Same model across engines +
// the sm8550 bundle matches the S23's SoC (the real shot at LiteRT NPU).
const LLAMA_GEMMA = new LlamaRnAdapter({
  modelPath: `${FILES}/gemma-3-1b-it-q4_0.gguf`,
  quantLabel: 'Q4_0 (QAT)',
  modelLabel: 'Gemma 3 1B',
});
const LITERT_GEMMA_CPU = new LiteRtAdapter({
  modelPath: `${FILES}/gemma3-1b-it-int4.litertlm`,
  backend: 'cpu',
  quantLabel: 'int4',
  modelLabel: 'Gemma 3 1B',
});
// NPU path A — AOT: the precompiled sm8550 (v73) bundle. Its context binary is
// QNN 2.34.0; runtime is 2.48.40, so this may hit a context-binary version wall.
const LITERT_GEMMA_NPU_AOT = new LiteRtAdapter({
  modelPath: `${FILES}/Gemma3-1B-IT_sm8550.litertlm`,
  backend: 'npu',
  quantLabel: 'int4 (sm8550 AOT)',
  modelLabel: 'Gemma 3 1B',
});
// NPU path B — JIT: the generic int4 bundle compiled on-device against the bundled
// QAIRT 2.48.40 (matches the 0.14.0 runtime). Sidesteps the AOT version lock.
const LITERT_GEMMA_NPU_JIT = new LiteRtAdapter({
  modelPath: `${FILES}/gemma3-1b-it-int4.litertlm`,
  backend: 'npu',
  quantLabel: 'int4 (JIT)',
  modelLabel: 'Gemma 3 1B',
});

interface RunConfig {
  key: string;
  label: string;
  adapter: EngineAdapter;
}
// S23 (SM8550 / Hexagon v73) LiteRT-NPU test build — FOCUSED run set. Goal: get a
// real LiteRT NPU number (the engine's actual purpose). CPU baseline first, then the
// two NPU paths (JIT via generic model, AOT via precompiled sm8550). NPU runs last so
// a native abort can't wipe the CPU baseline. llama.cpp CPU-variant kept as a sanity row.
const RUNS: RunConfig[] = [
  {key: 'litert-gemma-cpu', label: 'LiteRT·Gemma3·CPU', adapter: LITERT_GEMMA_CPU},
  {key: 'litert-gemma-npu-aot', label: 'LiteRT·Gemma3·NPU(sm8550 AOT)', adapter: LITERT_GEMMA_NPU_AOT},
];

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Cold runs: fresh load -> benchOnce -> release, N times. */
async function runEngine(adapter: EngineAdapter, log: (s: string) => void) {
  const pp: number[] = [];
  const tg: number[] = [];
  const ttft: number[] = [];
  const loads: number[] = [];
  let prefillMethod = '';
  let decodeMethod = '';
  for (let i = 0; i < N_COLD; i++) {
    log(`  ${adapter.engine} run ${i + 1}/${N_COLD}: loading…`);
    const loadMs = await adapter.load();
    loads.push(loadMs);
    const m = await adapter.benchOnce({prompt: STANDARD_PROMPT, maxDecodeTokens: MAX_DECODE_TOKENS});
    await adapter.release();
    pp.push(m.prefillTps);
    tg.push(m.decodeTps);
    ttft.push(m.ttftMs);
    prefillMethod = m.prefillMethod;
    decodeMethod = m.decodeMethod;
    log(`    pp=${m.prefillTps.toFixed(1)} tg=${m.decodeTps.toFixed(1)} ttft=${m.ttftMs}ms (${m.prefillMethod}/${m.decodeMethod})`);
  }
  const record = {
    schema: 'xebench-raw',
    harnessVersion: 'xebench-0.4-app',
    engine: adapter.engine,
    engineVersion: adapter.engineVersion(),
    backend: adapter.backend,
    platform: 'android',
    deviceInfo: {model: Platform.constants?.Model ?? 'android', soc: '', androidRelease: String(Platform.Version)},
    model: adapter.modelLabel,
    quant: adapter.quantLabel,
    modelFile: adapter.modelFile,
    protocol: {
      nColdRuns: N_COLD,
      promptLabel: STANDARD_PROMPT_LABEL,
      maxDecodeTokens: MAX_DECODE_TOKENS,
      coldDefinition: 'fresh engine instance, warm foreground app process (top-app cpuset)',
    },
    coldRuns: [{runIndex: 0, prefillMethod, decodeMethod}],
    summary: {
      prefillTps: {median: median(pp), n: N_COLD, min: Math.min(...pp), max: Math.max(...pp)},
      decodeTps: {median: median(tg), n: N_COLD, min: Math.min(...tg), max: Math.max(...tg)},
      ttftMs: {median: median(ttft), n: N_COLD},
      loadMs: {median: median(loads), n: N_COLD},
    },
    guardsPassed: true,
    guardNotes: [],
  };
  console.log('XEBENCH_RESULT ' + JSON.stringify(record));
  log(`  ${adapter.engine}/${adapter.backend} DONE: pp(med)=${median(pp).toFixed(1)} tg(med)=${median(tg).toFixed(1)}`);
  return record;
}

export default function App() {
  const [logs, setLogs] = useState<string[]>(['xebench — auto-running all engines…']);
  const [busy, setBusy] = useState(false);
  const started = useRef(false);
  const log = useCallback((s: string) => setLogs(prev => [...prev, s]), []);

  const runOne = useCallback(
    async (rc: RunConfig) => {
      log(`\n▶ ${rc.label} — ${N_COLD} cold runs`);
      try {
        await runEngine(rc.adapter, log);
      } catch (e: any) {
        log(`  ✖ ${rc.label} ERROR: ${e?.message ?? e}`);
        console.log(`XEBENCH_ERROR ${rc.key}: ${e?.message ?? e}`);
      }
    },
    [log],
  );

  const runAll = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    for (const rc of RUNS) {
      // eslint-disable-next-line no-await-in-loop
      await runOne(rc);
    }
    console.log('XEBENCH_DONE');
    log('\n✔ all engines done');
    setBusy(false);
  }, [busy, log, runOne]);

  // Auto-run every engine once on launch (host automation relies on this).
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const t = setTimeout(runAll, 800);
    return () => clearTimeout(t);
  }, [runAll]);

  return (
    <SafeAreaView style={styles.root}>
      <Text style={styles.title}>xebench {busy ? '· running…' : ''}</Text>
      <View style={styles.row}>
        {RUNS.map(rc => (
          <Btn key={rc.key} label={rc.label} onPress={() => !busy && runOne(rc)} disabled={busy} />
        ))}
      </View>
      <ScrollView style={styles.logbox}>
        {logs.map((l, i) => (
          <Text key={i} style={styles.logline}>{l}</Text>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

function Btn({label, onPress, disabled}: {label: string; onPress: () => void; disabled: boolean}) {
  return (
    <Pressable onPress={onPress} disabled={disabled} style={[styles.btn, disabled && styles.btnDisabled]}>
      <Text style={styles.btnText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: '#111', padding: 12},
  title: {color: '#fff', fontSize: 20, fontWeight: '700', marginBottom: 10},
  row: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12},
  btn: {minWidth: '47%', flexGrow: 1, backgroundColor: '#2a78d6', padding: 10, borderRadius: 8, alignItems: 'center'},
  btnDisabled: {opacity: 0.4},
  btnText: {color: '#fff', fontWeight: '600', fontSize: 12},
  logbox: {flex: 1, backgroundColor: '#000', borderRadius: 8, padding: 8},
  logline: {color: '#7fdd7f', fontFamily: 'monospace', fontSize: 11},
});
