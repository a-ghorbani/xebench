# xebench — cross-engine on-device LLM benchmark harness

**Which inference engine is actually fastest on which phone?** xebench runs the *same model, same prompt, same device, same protocol* across multiple on-device LLM engines and backends, so the numbers are comparable instead of scattered across each vendor's own README with its own quant and its own device.

It powers the cross-engine leaderboard at **[pocketpal.dev/leaderboard/engines](https://pocketpal.dev/leaderboard/engines)**.

> **Read [METHODOLOGY.md](./METHODOLOGY.md) first** — it's the fairness contract (Rule 0: measure in a foreground app, never a throttled adb-shell binary), and every rule is enforced in code.
> **Read [ENGINES.md](./ENGINES.md)** — why each engine exists, where it shines, and whether we actually measure it there.

## Engines & coverage

| Engine | Via | CPU | GPU | NPU |
|---|---|---|---|---|
| **llama.cpp** | `llama.rn` | ✅ | ✅ OpenCL (Adreno) | ✅ Hexagon (`ggml-hexagon`) |
| **ExecuTorch** | `react-native-executorch` | ✅ XNNPACK | — | — (delegate not in RN binding) |
| **LiteRT-LM** | native Kotlin bridge | ✅ | ⛔ ML Drift no-OpenCL | ⛔ blocked — Google withholds prebuilt bridge libs ([#6889](https://github.com/google-ai-edge/LiteRT/issues/6889)) |
| **GENIE-X / Genie** | `geniex-android` / QAIRT `genie-t2t-run` | ✅ | — | ✅ Hexagon (QNN context binary) |

## Headline findings so far

- **There is no single "fastest engine."** The ranking flips by model *and* by metric: LiteRT wins Gemma prefill; llama.cpp wins Qwen decode; ExecuTorch wins Llama prefill; llama.cpp wins Llama decode.
- **NPU is not automatically faster.** On a Snapdragon 8 Elite Gen 5 (Hexagon v81), for Llama 3.2 1B, the NPU *wins prefill outright* but *loses decode badly* to the CPU — the Oryon cores are strong and decode is memory-bandwidth-bound.
- **Same NPU, opposite outcomes for the two "NPU-first" engines.** Give Qualcomm's Genie a device+version-matched context binary and it runs end-to-end. LiteRT-LM can't — Google ships the runtime but not the prebuilt Qualcomm bridge libraries. One vendor shipped everything; the other didn't. (This is the load-bearing story — see ENGINES.md.)

## Architecture

```
xebenchapp/App.tsx ─ auto-runs every configured engine ─► EngineAdapter
                                                          ├─ LlamaRnAdapter       (llama.rn)
                                                          ├─ ExecutorchAdapter    (react-native-executorch)
                                                          ├─ LiteRtAdapter        (native LiteRtBenchModule.kt)
                                                          └─ GenieXAdapter        (native GenieXBenchModule.kt)
                                          │  logs XEBENCH_RESULT <json> per engine
                                          ▼
   scripts/capture_app_run.sh  ─ scrapes logcat, enriches device info ─► results/raw-*.jsonl
                                          ▼
   scripts/aggregate.mjs --update  ─ raw JSONL → upsert into ─► data/benchmarks.json  (canonical, published)
                                                                       │  loaded dynamically by
                                                                       ▼  pocketpal.dev/leaderboard/engines
```

It's a **React Native app** (not adb-shell binaries) on purpose: on non-rooted Android, adb-shell processes run in the throttled `background` cpuset and report numbers ~5–10× low. A foreground installed app gets the `top-app` cpuset and real clocks. See METHODOLOGY Rule 0.

Every published number traces back to a raw JSONL line carrying device, SoC, thermal state, and engine version.

## Quickstart

```bash
cd xebenchapp
npm install                       # Node 20+ (Metro needs toReversed)

# stage models on the device's app dir (see MODELS.md for the exact files)
adb -s <serial> push <model files> /storage/emulated/0/Android/data/com.xebenchapp/files/

# build + install a RELEASE build (debug is unfair — see METHODOLOGY)
cd android && ./gradlew assembleRelease
adb -s <serial> install -r app/build/outputs/apk/release/app-release.apk

# run: the app auto-runs all engines on launch and logs XEBENCH_RESULT/XEBENCH_DONE
../../scripts/capture_app_run.sh <serial>       # scrapes logcat → results/raw-*.jsonl

# aggregate into the canonical published results file, then commit to publish
node ../../scripts/aggregate.mjs ../../results/raw-*.jsonl --update
```

## Native dependencies (you must obtain these yourself)

This repo does **not** vendor model weights or proprietary vendor SDKs.

- **Models** — GGUF / `.pte` / `.litertlm` / QNN context binaries. See **[MODELS.md](./MODELS.md)** for exact files and sources.
- **QAIRT SDK** (Qualcomm AI Runtime) — needed for the LiteRT-NPU and Genie-NPU paths. Download the *Community* track (direct download) from Qualcomm; the `lib/aarch64-android/` host libs + the matching `lib/hexagon-vNN/unsigned/libQnnHtpVNNSkel.so` go into the app's `jniLibs/` (git-ignored — Qualcomm-licensed, do not redistribute).
- **GENIE-X** (`com.qualcomm.qti:geniex-android`) — Qualcomm Maven dependency.
- **Genie context binaries** — per-SoC, per-QAIRT-version compiled `.bin` bundles (e.g. from `runanywhere/genie-npu-models` or Qualcomm AI Hub). They must match *both* your device's Hexagon arch (v73/v75/v79/v81) *and* a QAIRT version you can obtain.

### Toolchain gotchas (hard-won)
- **Node 20+** (RN 0.82 Metro).
- Host is often aarch64 Linux — point `hermesCommand` at the x86-64 hermesc (runs via emulation).
- **Kotlin 2.3.0** required by `litertlm-android` (pin `kotlinVersion` *and* the plugin classpath).
- `litertlm-android` needs `useLegacyPackaging` / `extractNativeLibs=true` so QNN libs land in `nativeLibraryDir`.
- **Build variants don't coexist:** llama.rn's Hexagon lib and GENIE-X's bundled Hexagon runtime collide, and llama.rn hijacks `ADSP_LIBRARY_PATH`. Toggle via `android/app/build.gradle` packaging excludes + `App.tsx` RUNS. (Turning this into a proper Gradle flavor is a good first contribution — see below.)

## Results are published here — the website loads them dynamically

**[`data/benchmarks.json`](./data/benchmarks.json) is the canonical, published results file** and the single source of truth. It's a versioned envelope (`schemaVersion`, `generatedAt`, `source`, `rows[]`) validated by **[`data/benchmarks.schema.json`](./data/benchmarks.schema.json)**. Each row carries its `provenance` (`measured` = our lab runs · `vendor` = engine-maker official numbers), `quant`, source link, and methodology caveats (`asStated`).

**Publishing a new measurement is a one-liner — no website change:**

```bash
node scripts/aggregate.mjs results/raw-*.jsonl --update   # upsert measured rows into data/benchmarks.json
git commit -am "results: <device> <engine>" && git push   # that's it
```

`--update` reads `data/benchmarks.json`, upserts the freshly-measured rows (keyed by engine·backend·platform·device·model·quant·provenance, so re-runs overwrite in place), re-stamps `generatedAt`, and writes it back. Existing rows (other devices, vendor citations) are preserved.

**Consumers load this file dynamically** — the pocketpal.dev leaderboard fetches it from the repo (raw URL / CDN) with periodic revalidation and overlays live crowd data, so a commit here surfaces on the board with no redeploy or dashboard-dev work. The stable interface is the schema; bump `schemaVersion` on any breaking change. (`--pretty` without `--update` still just prints `EngineBenchmarkRow[]` to stdout for ad-hoc use.)

## Contributing

Good first issues:
- Turn the manual build-variant toggle into a proper Gradle **product flavor** (`mainVariant` / `geniexVariant` / `litertNpuVariant`).
- Add a **quality metric** (perplexity or a small task-accuracy set) so the board isn't speed-only — the top methodology gap (see METHODOLOGY §quant).
- New device rows (MediaTek / more Snapdragons), new engines (MLX/CoreML need an iOS harness).
- A Rule-0-clean, in-app Genie NPU path (currently CLI-measured).

Please keep every number traceable to a raw JSONL record and label its provenance and quant honestly — that transparency is the whole point.

## License

[Apache-2.0](./LICENSE). Note that model weights and vendor SDKs referenced here carry their **own** licenses (Qualcomm QAIRT, Meta Llama, Google Gemma, etc.) — obtain and use them under those terms.
