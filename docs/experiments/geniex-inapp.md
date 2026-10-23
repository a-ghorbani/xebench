# GENIE-X in the app (geniex-android 0.8.0)

Lab notes for the model refresh ([#43](https://github.com/a-ghorbani/xebench/issues/43)): GENIE-X
measured in the installed app on the POCO F8 Ultra (SM8850) NPU, for Qwen3 1.7B with Qualcomm's
`geniex_qairt` W4A16 bundle. Everything below was read from the geniex-android 0.8.0 AAR (Maven
Central), its sources at the `v0.8.0` tag of [qualcomm/GenieX](https://github.com/qualcomm/GenieX)
(`a5c8e7f`) and the QAIRT plugin it pins ([qualcomm/geniex-qairt-plugin](https://github.com/qualcomm/geniex-qairt-plugin)
`a80a890`), and the bundle itself. No phone has run this path yet; the device spike decides the
open points at the end.

## What runs

| Backend | GENIE-X plugin | Model | Role |
| --- | --- | --- | --- |
| NPU | `qairt`: geniex-qairt's own LLM pipeline on the QNN C API (no `libGenie.so`) | the QAIRT bundle directory (`genie_config.json`, four QNN context binaries, tokenizer) | measured |
| CPU | `llama_cpp`: the llama.cpp GENIE-X bundles | `unsloth/Qwen3-1.7B-GGUF` Q4_0 | output-gate reference only |

`GenieXBenchModule.kt` (geniex flavor) creates one model per load (`LlmCreateInput`: runtime
`qairt` + compute unit `npu`, or `llama_cpp` + `cpu`), runs one request per repetition and destroys
the model at release. It calls the SDK's JNI class `com.geniex.sdk.jni.Llm` through
`GenieXNative.java` instead of the public `LlmWrapper`, which runs the same calls on a coroutine
dispatcher and forwards tokens and the final `ProfilingData` through a buffered `callbackFlow`
(`trySend`): that hops threads before the app sees a token and can drop the completion event.
`Llm` is Kotlin `internal`; Java can call it. The shim refuses any packaged geniex-android other
than 0.8.0. geniex-android 0.8.0's POM declares no dependencies (the SDK is built against
kotlinx-coroutines 1.7.3); the bridge uses none of its coroutine wrappers.

Every request:

- **sends the pre-templated prompt as the raw prompt.** `Llm.generate(prompt)` applies no chat
  template. The QAIRT pipeline tokenizes the string with the bundle's `tokenizer.json` and, on the
  first turn, prepends the `bos-token` of the bundle's `genie_config.json` unless the prompt already
  starts with it (the default text pipeline, `auto_llm::makePipeline` with `prepend_bos`). This
  Qwen3 bundle's `bos-token` is 151643 (`<|endoftext|>`), so the NPU prompt is the text plus that
  one token; the pipeline logs it (`prompt: N tokens, first-turn BOS=true, ids start with
  [151643, …]`). The llama_cpp plugin uses `common_tokenize(…, add_special=true,
  parse_special=true)`, as llama.rn does, and adds a BOS only when the GGUF asks for one, which
  the Qwen3 GGUF does not. On the POCO F8 Ultra the std-pp512-v1 prompt is 520 tokens on the NPU
  and 519 on the CPU; each run records its own count, and prefill speed uses it.
- **is greedy.** Both plugins treat a temperature below zero as argmax; the bridge passes −1 with
  top-k 1. Temperature 0 would not be greedy: the QAIRT plugin reads 0 as "use the bundle's
  sampler", which is `temp 0.8, top-k 40, top-p 0.95, seed 42` in this bundle's `genie_config.json`.
- **starts from an empty context.** The QAIRT plugin is multi-turn by default (each `generate`
  appends to the KV cache), so the bridge calls `Llm.reset` before every request; it matters for
  the output gate, which sends five probes to one load.
- **carries no stop words** and decodes at most `maxDecodeTokens` (128).

### Decode length: GENIE-X 0.8.0 cannot decode past the end of turn

`GenerationConfig` has `maxTokens`, `stopWords`, `samplerConfig`, media paths and the sliding
window, and nothing that ignores the end of turn. The QAIRT pipeline's decode loop
(`LLMModel::generate`) breaks as soon as the sampled token is an end of generation: one of the
bundle's `eos-token` ids or a token the tokenizer marks as end of generation. The llama_cpp
plugin stops on `llama_vocab_is_eog`. So a repetition decodes up to 128 tokens and stops at the end
of turn, like llama.cpp rows; `decodeTokens` records what it produced. With std-pp512-v1 (a Llama
3.2 turn sent as text) Qwen3 may or may not reach 128; the spike records it.

### Timings: what each number is and where it comes from

| Field | Value | Source |
| --- | --- | --- |
| `loadMs` | wall time of `Llm.create` | bridge, `System.nanoTime` around create (QNN backend and device setup, the four context binaries created from file, graphs retrieved; for the CPU, the GGUF read into memory: the llama_cpp plugin does not mmap on Android) |
| `ttftMs` | request → first non-empty token callback | bridge, on its worker thread, as LiteRT-LM's: includes JNI, tokenization, prefill and the first sampling step |
| `promptTokens` | the engine's prompt token count | `ProfilingData.promptTokens` (QAIRT: tokens fed to the model; llama_cpp: tokens prefilled) |
| `prefillTps` (NPU) | promptTokens ÷ GENIE-X's prompt time | `ProfilingData.promptTimeMs`, which the QAIRT plugin sets to its own TTFT ("approximate", `qairt/src/llm.cpp`): native clock from after tokenization to the first sampled token, so prefill plus one sampling step. Method `ttft-derived`. |
| `prefillTps` (CPU) | promptTokens ÷ prefill time | llama_cpp plugin's profiler around the prefill `llama_decode` batches only. Method `engine-timings`. |
| `decodeTokens` | tokens produced | `ProfilingData.generatedTokens`, minus one on the NPU when it stopped at the end of turn: the QAIRT plugin counts the end-of-turn token (Genie's `num-generated-tokens` convention) although it is never produced |
| `decodeTps` | decodeTokens ÷ decode time | `ProfilingData.decodeTimeMs`: QAIRT, first produced token → end of generation (one forward pass per produced token in that window); llama_cpp, end of prefill → end of generation. Method `engine-timings`. |
| `engineInternal` | `promptMs`, `predictedMs`: GENIE-X's own times; speeds as above | GENIE-X's own `decodingSpeed` differs only by the counted end-of-turn token |

The adapter checks that GENIE-X's own speeds equal its counts over its times and that the QAIRT
prompt time equals its TTFT, and fails the repetition otherwise: a later SDK that reports
something else must not be read with these definitions.

## QAIRT: the bundle wants 2.50; the AAR ships 2.45

- The bundle is built with QAIRT 2.50.0.260828221209 (`metadata.json`, `release_assets.json`).
- geniex-android 0.8.0 packages the QAIRT **2.45.0.260326154327** QNN runtime (`libQnnHtp.so`,
  `libQnnHtpV81Skel.so`, … carry that build string). Its model manager defaults to AI Hub
  release v0.60.0 (`GENIEX_AIHUBVERSION`), not v0.65.0.
- GENIE-X documents a runtime override (`GenieXSdk.setQairtRuntimePath`, notes/run.md) and warns
  that "a bundle built for one QAIRT version may not load on another — that surfaces as a QNN
  context error from `geniex_llm_create`". On Android 0.8.0 the override cannot work: the class
  initializer of `GenieXSdk` loads `libnpu_jni.so`, whose `JNI_OnLoad` calls `geniex_init`, which
  locks the path (`geniex_set_qairt_runtime_path` then returns `ALREADY_INITIALIZED`).
- On Android the QAIRT plugin loads QNN from the directory of `libgeniex_core.so` (flat layout),
  which is the APK's native library directory, and points `ADSP_LIBRARY_PATH` there for the skel.

So the geniex flavor packages QAIRT 2.50 in place of the AAR's copies:
`scripts/stage_qairt_libs.py --flavor geniex` stages the 2.50 build of all 30 QNN runtime files the
AAR carries into `app/src/geniex/jniLibs/arm64-v8a` (git-ignored), `app/build.gradle` picks the
project's copies first (`pickFirsts`) and keeps them unstripped, and
`check_reference_apk.py --flavor geniex --qairt-version 2.50.0.260828221209 --qairt-libs …/qairt-libs.json`
checks that every packaged QNN library names that build and every staged file is packaged byte
for byte. A geniex release build fails without that staging (`checkGeniexQairtStaging` rechecks
`qairt-libs.json`: flavor geniex, QAIRT 2.50.0, every file's SHA-256), so an unstaged build cannot
ship QAIRT 2.45 by accident; `-Pxebench.geniexAarQairt=true` builds with the AAR's runtime on
purpose (the 2.45 comparison build below). The plugin negotiates the QNN C API at load time (floor: QAIRT 2.36 headers), so a newer
runtime than the bundled one is a supported combination. `runtimeVersions.qairt` records the build
string of the packaged `libQnnHtp.so`, which is the runtime that loads.

## The bundle

| | |
| --- | --- |
| Source | `qualcomm/Qwen3-1.7B` `release_assets.json` at `72c5712` (version 0.65.0), asset `w4a16` / `qualcomm-snapdragon-8-elite-gen5-for-galaxy` / `geniex_qairt` |
| Zip | `qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy.zip`, 1,538,593,271 bytes, SHA-256 `ab021d189e387ba417e9156fb8d5fcee7a98cf1befd899a8e6f2a9b65dbf9b2b`, S3 object version `Yb0NrZDlpe5O5Hc3SM4NUGSYelSDiEle` |
| Pins | both the zip and the unpacked directory (bundle digest) in `configs/models/experimental-models.json` `pinnedArtifacts` and MODELS.md, so the publication policy's artifact check can pass for a GENIE-X NPU session |
| Unpacked | 11 files, 1,764,416,101 bytes; four context binaries `part1_of_4.bin` … `part4_of_4.bin` |
| Bundle digest | `c41b7071b62e791b924254f1e8ed3245ee74825013c1723e3a62e8877b2ab5c2` (the run's `modelSha256`) |
| Target | `htp_backend_ext_config.json`: `soc_model` 87, `dsp_arch` v81, burst, one core, shared buffers, weight sharing; `metadata.json` chipset aliases `qualcomm-snapdragon-8-elite-gen5-for-galaxy`, `sm8850-ad`; reference device "Samsung Galaxy S26 (Family)" |
| Context | 4096 (`genie_config.json`), BOS 151643, EOS 151645 (`<\|im_end\|>`) |

The bundle digest is the SHA-256 of the bundle's `sha256sum` listing (one line per file, names in
byte order); `ls | LC_ALL=C sort | xargs sha256sum -- | sha256sum` in the bundle directory
reproduces it, and the app computes it before measuring (`src/harness/bundle.ts`), so a
configuration pins the bundle with `modelSha256` like a GGUF.

`scripts/stage_geniex_bundle.py` downloads the zip (or takes `--zip`), checks its size and SHA-256,
refuses any member outside the bundle directory, any link and any file it does not pin, unpacks
into `models/geniex/` (git-ignored), checks every file and the digest, and writes
`geniex-bundle.json` beside it. `--check` re-verifies; `--push --serial …` copies it to
`/sdcard/Android/data/com.xebenchapp/files/<bundle>.partial`, hashes every file on the phone,
only then renames it into place, and sets the directory to mode 2775 and checks it: adb creates it
owned by `shell` with mode 2770 (group `ext_data_rw`), which the app's uid cannot enter, so model
creation fails. `check_reference_apk.py` refuses any bundle file in any flavor,
by name and by content.

## Output gate: GENIE-X's llama_cpp plugin is the reference

The accelerator protocol compares a candidate with a CPU run of the **same engine**. GENIE-X's
`llama_cpp` plugin on the `unsloth/Qwen3-1.7B-GGUF` Q4_0 file is that run, in the same APK:

- it is the file Qualcomm's own card lists as GENIE-X's llama.cpp asset for this model
  (`GENIEX_LLAMACPP`, q4_0), and the GGUF the llama.cpp rows of the refresh use;
- it goes through the same SDK request path as the NPU run (raw prompt, greedy sampler, no stop
  words, stop at the end of turn), so a difference between the two is the backend, the
  quantization and the BOS token the QAIRT pipeline adds, not the harness;
- llama.rn CPU would also fit in the APK, but as another engine it needs a cross-engine reference
  rule the protocol does not have.

The files differ (Q4_0 GGUF against W4A16 context binaries), so `sameArtifact` is false. On
Android the plugin registers ggml's Hexagon backend whatever the device (`htp_backend_present`);
`n_gpu_layers` 0 and the CPU device keep the model on the CPU.

## Backend evidence: geniex-npu-log-v1, pending device confirmation

GENIE-X does not report where it ran. Its process-library evidence is like LiteRT-LM's: the first
NPU load in a process maps `libQnnHtp.so` (`consistent`); later loads find it mapped (`unknown`).
The app's own log is stronger. geniex-android routes its SDK and plugin log, QNN's own messages
included, to logcat under the tag `GenieXSdk` (release builds log the message alone; debug builds prefix
`[file:line:func]`), and FastRPC logs under the app's process name. The rule
(`scripts/backend_log.py`) needs, inside each repetition's window:

| Event | Line (geniex-android 0.8.0) | What it shows |
| --- | --- | --- |
| `htp-runtime` | `HTP runtime path: … (auto-resolved from geniex_core directory (flat layout))` | the QNN runtime came from the APK, not an override |
| `htp-arch` | `HTP arch v81 detected.` | the compute DSP answered a FastRPC query with its HTP architecture |
| `htp-graphs` | `HTP graphs will execute on 1 core (default; …)` or `HTP multicore enabled: …` | logged only after every context binary of the load was created on the HTP backend and its graphs retrieved |
| `prompt` | `prompt: N tokens, first-turn BOS=…, ids start with […]` | the request was prefilled by the QAIRT pipeline (the llama_cpp plugin never logs it) |

and, once in the capture, `htp-skel-cdsp`: FastRPC's `remote_handle64_open: opened handle …
libQnnHtpV81Skel.so … _dom=cdsp`, the same line litert-npu-log-v1 uses. The formats come from the
sources and the strings in the shipped `libgeniex_core.so`; no device log has shown them yet, so
`backend_log.PENDING_DEVICE_CONFIRMATION` holds the rule: captures keep its events, bundles carry
them and the policy reports the result, but a GENIE-X NPU backend stays `unknown` until a reviewed
change removes the rule from that set after the spike.

## Genie's deprecation

The model card says: "See the LLM-on-Genie tutorial to run with the Genie runtime. Note: Genie
support will be deprecated soon." That is the `genie` asset type and its runtime (`libGenie.so`,
`genie-t2t-run`), which the July 2026 GENIE-X rows used through the CLI. The `geniex_qairt` assets
run on GENIE-X's own pipeline over the QNN C API and do not load `libGenie.so`. For xebench: the
in-app path does not depend on Genie; the CLI path is the one that may stop getting new bundles.
`release_assets.json` 0.65.0 still lists a `genie` zip next to each `geniex_qairt` one.

## For the device spike (POCO F8 Ultra)

1. Build `assembleGeniexRelease` from a clean commit with QAIRT 2.50 staged; check it with
   `check_reference_apk.py --flavor geniex --qairt-version 2.50.0.260828221209 --qairt-libs app/src/geniex/jniLibs/qairt-libs.json`.
2. Stage the bundle (`stage_geniex_bundle.py`, then `--push --serial …`) and the Q4_0 GGUF
   (`stage_model.py`, once its pin lands).
3. Run a configuration like the one below (it needs the `qwen3` gate template and the GGUF pin).
4. Check, from the capture: the bundle loads on a non-Galaxy SM8850 (a refusal is a QNN context
   error from create, or a `soc_model` complaint); every geniex-npu-log-v1 line appears in each
   repetition window with the format above; `runtimeVersions.qairt` is 2.50.0.260828221209; how
   many tokens Qwen3 decodes; and the gate result against the CPU reference. If the bundle
   refuses QAIRT 2.50 on this phone, build once with `-Pxebench.geniexAarQairt=true` (the AAR's
   QAIRT 2.45) to separate a version problem from a chipset one.

```json
{
  "schemaVersion": 2, "id": "f8-qwen3-geniex-npu-v1", "nColdRuns": 3, "cooldownSec": 120,
  "readinessTimeoutSec": 900, "maxBatteryTempRiseC": 2, "maxDecodeTokens": 128,
  "chatTemplate": "qwen3", "measuredPrompt": "std-pp512-v1",
  "runs": [
    {"id": "qwen3-1.7b-q40-geniex-cpu", "engine": "genie-x", "backend": "cpu", "purpose": "measurement",
     "model": "Qwen3 1.7B", "modelFile": "Qwen3-1.7B-Q4_0.gguf", "quant": "Q4_0", "nThreads": 4, "nCtx": 4096,
     "referenceRunId": null, "modelSha256": "<the unsloth Q4_0 pin>"},
    {"id": "qwen3-1.7b-w4a16-geniex-npu", "engine": "genie-x", "backend": "npu", "purpose": "measurement",
     "model": "Qwen3 1.7B", "modelFile": "qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy",
     "quant": "W4A16", "nThreads": null, "nCtx": null, "referenceRunId": "qwen3-1.7b-q40-geniex-cpu",
     "modelSha256": "c41b7071b62e791b924254f1e8ed3245ee74825013c1723e3a62e8877b2ab5c2"}
  ]
}
```
