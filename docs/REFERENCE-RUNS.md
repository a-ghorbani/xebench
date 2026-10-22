# CPU reference runs

The default app runs the versioned configuration in
[`cpu-reference-v2.json`](../xebenchapp/configs/cpu-reference-v2.json), which every flavor
bundles. [`cpu-reference-v3.json`](../xebenchapp/configs/cpu-reference-v3.json), the
configuration chosen for the board's llama.cpp CPU rows, and
[`cpu-reference-v1.json`](../xebenchapp/configs/cpu-reference-v1.json) run as staged
configurations. All three record sessions of protocol `cpu-reference-v3`; the
configuration IDs are separate from the protocol versions of the same names described
under [Host validation](#host-validation). This first reference path supports
**llama.cpp CPU**.

cpu-reference-v1 and v2 run one model file, Llama 3.2 1B Instruct Q4_K_M. cpu-reference-v3
runs three: Q4_K_M, Q4_0 and Q8_0 of the same model, from the same pinned revision
([MODELS.md](../MODELS.md)). The configurations differ in how llama.rn loads the model and
computes attention:

| Run field | llama.rn parameter | cpu-reference-v1 | cpu-reference-v2 | cpu-reference-v3 |
| --- | --- | --- | --- | --- |
| `useMmap` | `use_mmap` | not set (`true`) | `false` | `false` |
| `useMlock` | `use_mlock` | not set (`false`) | `false` | `false` |
| `noExtraBufts` | `no_extra_bufts` | not set (`false`, weight repacking on) | `false` | `false` |
| `flashAttnType` | `flash_attn_type` | not set (`auto`) | `off` | `auto` |

Two configuration fields also differ. One cpu-reference-v3 invocation runs 9 repetitions
against one battery-temperature baseline, so it cools longer between repetitions and waits
longer for readiness; the 2 °C rise bound (`maxBatteryTempRiseC`) is the same in all three.

| Configuration field | cpu-reference-v1 | cpu-reference-v2 | cpu-reference-v3 |
| --- | --- | --- | --- |
| `cooldownSec` | 30 | 30 | 120 |
| `readinessTimeoutSec` | 180 | 180 | 900 |

The board's other llama.cpp setups use the same settings and timing in their own files.
[`cpu-reference-v3-qwen25.json`](../xebenchapp/configs/cpu-reference-v3-qwen25.json) runs
Qwen2.5 1.5B Instruct Q8_0 and
[`cpu-reference-v3-gemma3.json`](../xebenchapp/configs/cpu-reference-v3-gemma3.json) runs
Gemma 3 1B Q4_0 (QAT) on the CPU ([MODELS.md](../MODELS.md)). The GPU and NPU rows run
through the accelerator protocol, which adds the output-correctness gate against a CPU run of
the same file ([litert-accelerators](experiments/litert-accelerators.md)):
[`llamacpp-qwen25-opencl-v1.json`](../xebenchapp/configs/llamacpp-qwen25-opencl-v1.json)
(Qwen2.5 1.5B Q8_0, OpenCL) and
[`llamacpp-llama32-q4km-hexagon-v1.json`](../xebenchapp/configs/llamacpp-llama32-q4km-hexagon-v1.json)
(Llama 3.2 1B Q4_K_M, Hexagon `HTP0`). Accelerator runs take the same optional load fields as
reference runs; these set cpu-reference-v3's values on every run, the gate's CPU references
included.

Every llama.cpp and LiteRT-LM board row measures the same prompt text with a 128-token decode
budget (LiteRT-LM rows decode exactly 128 tokens; see below). An accelerator configuration's
`measuredPrompt` selects what its repetitions measure: `std-chat-v1` (the default) is the
accelerator body as one user turn in the configuration's chat template; `std-pp512-v1` is the
CPU reference prompt, pre-templated (`promptFormat: raw-pretemplated`). These two
configurations set `std-pp512-v1`, which is a Llama 3.2 turn for every model, as on the CPU
rows. The output-correctness gate keeps its own probes in each model's chat template
(`chatTemplate: qwen25`, a ChatML user turn without a system message, and `llama32`). In an
accelerator configuration that also has a GPU or NPU run, the CPU run is the gate's reference, not
a board CPU measurement: only that configuration's GPU and NPU sessions are displayed.

An accelerator configuration's `chatTemplate` is one of `gemma3`, `gemma4`, `llama32`, `qwen25`
and `qwen3` ([`prompts.ts`](../xebenchapp/src/harness/prompts.ts)). Each is one user turn without
a system message, exactly as the model's own chat template renders it with the generation prompt;
the tokenizer adds the BOS token. `gemma4` and `qwen3` have thinking off: Gemma 4's template
thinks only when asked to, and `qwen3` is the turn Qwen3's template renders with
`enable_thinking: false`, which ends with an empty think block.

### LiteRT-LM board configurations

The board's LiteRT-LM rows run through the accelerator protocol with cpu-reference-v3's
timing (3 repetitions, 120 s cooldown, 900 s readiness timeout, 2 °C rise bound, 128 decode
tokens) and `measuredPrompt: std-pp512-v1`, so they measure the text the llama.cpp CPU rows
measure. llama.rn receives that text as a raw prompt. LiteRT-LM has no raw-prompt call with
engine counters, so `LiteRtBenchModule.kt` sends it as the user message of a conversation whose
chat template renders message text verbatim (no role markers, no generation prompt), checks that
the rendered prompt equals the text byte for byte, and refuses the repetition otherwise.
LiteRT-LM adds the artifact's start token on the first turn when its metadata names one, as
llama.cpp adds BOS when the model asks for it; each engine records its own prompt token count.
On the identical prompt text, LiteRT-LM counts 519 tokens for Gemma 3 1B and 520 for Qwen2.5 1.5B;
llama.cpp counts 518 and 519. CPU runs use 4 threads. The files
are pinned in [MODELS.md](../MODELS.md#litert-lm-files).

Every repetition decodes exactly 128 tokens (`decodeMode: fixed`), past the model's end of turn,
so decode speed is always measured over the same number of tokens; the row's note says
"decode 128 tok, end of turn ignored". (A session without `decodeMode` whose median repetition
stopped at the end of turn before the budget says "decode N of 128 tok, stopped at the end of
turn"; one that reached the budget says "decode 128 tok".) LiteRT-LM's public API always stops at the artifact's stop
tokens. Its engine decodes a fixed number of steps only when created for benchmarking with a
decode count, so [`LiteRtFixedDecode.kt`](../xebenchapp/android/app/src/experimental/java/com/xebenchapp/LiteRtFixedDecode.kt)
creates the measured engines with `nativeCreateBenchmark` (prefill count 0, so the real prompt is
used; decode count 128) and runs a greedy conversation on them with the same pass-through
template, render check and streaming time to first token. These calls are internal to
litertlm-android and pinned to 0.18.0: the app refuses fixed decoding with another version or a
missing entry point, and a repetition that reports another decode length. The output gate
checks the same file on the same backend through LiteRT-LM's public engine API, with the engine's
own stopping rule; the measured repetitions use engines created with LiteRT-LM's benchmark
parameters. The benchmarking engine takes no thread count; LiteRT-LM's CPU
default is 4 threads, which is what the configurations state.

| Configuration | Runs | Board backend | Phones |
| --- | --- | --- | --- |
| [`litertlm-gemma3-int4-cpu-v2`](../xebenchapp/configs/litertlm-gemma3-int4-cpu-v2.json) | Gemma 3 1B `int4`, CPU | `cpu` | POCO F8 Ultra, Galaxy S23 |
| [`litertlm-qwen25-q8-cpu-v2`](../xebenchapp/configs/litertlm-qwen25-q8-cpu-v2.json) | Qwen2.5 1.5B `Q8`, CPU | `cpu` | POCO F8 Ultra, Galaxy S23, Pixel 9 |
| [`litertlm-gemma3-sm8850-npu-v2`](../xebenchapp/configs/litertlm-gemma3-sm8850-npu-v2.json) | Gemma 3 1B `int4` on CPU (gate reference), the SM8850 NPU bundle on NPU | `npu` | POCO F8 Ultra |

The `-v1` configurations are the same runs without `decodeMode`; their sessions are in the
history, and the board lists the `-v2` configurations only.

The NPU run is gated against the CPU run of the int4 file (`sameArtifact: false`). The Galaxy
S23 has no LiteRT-LM NPU configuration: its SM8550 bundle answers the gate's long probes wrong
([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508);
[litert-accelerators](experiments/litert-accelerators.md)). LiteRT-LM reports no executing
backend; an NPU session's backend is `confirmed` from the app's own log of each repetition's load
([RUN-CONDITIONS.md](RUN-CONDITIONS.md#litert-lm-npu-backend-confirmed-by-the-apps-log-litert-npu-log-v1)).

Sessions per setup (one configuration on one phone), fixed before any measurement:

1. Run the configuration once.
2. If that session does not pass (fewer than 3 of 3 repetitions, a condition verdict other than
   `pass`, or an output gate other than `pass`), run it once more, unchanged, on the same phone
   and APK.
3. Publish every session run under these configurations to the history. The board shows each
   key's newest verified session, else its newest unverified one (the selection rule); no
   session is left out by hand.
4. A setup with no passing session after the second run is not run again; its key is retired
   in [`configs/board.json`](../configs/board.json) with the reason.

Run one configuration on one phone with the experimental APK (built as in
[litert-accelerators](experiments/litert-accelerators.md#build)) and its files staged:

```sh
D=/sdcard/Android/data/com.xebenchapp/files
adb -s <serial> shell "[ -f $D/xebench-config.json ] && cp $D/xebench-config.json $D/xebench-config.json.litert-backup"
adb -s <serial> push xebenchapp/configs/litertlm-qwen25-q8-cpu-v2.json $D/xebench-config.json
python3 scripts/capture_app_run.py <serial> 3600
adb -s <serial> shell "if [ -f $D/xebench-config.json.litert-backup ]; then mv $D/xebench-config.json.litert-backup $D/xebench-config.json; else rm $D/xebench-config.json; fi"
python3 scripts/export_evidence.py export --out <dir> <capture dir>
python3 scripts/export_evidence.py verify <bundle dir>
```

The board shows these sessions once `configs/board.json` declares the LiteRT-LM set
([RUN-CONDITIONS.md](RUN-CONDITIONS.md#publishing-a-new-set)). The CPU run of
`litertlm-gemma3-sm8850-npu-v2` goes into the history only, with
`--allow-unlisted-configuration`.

### ExecuTorch configurations

ExecuTorch runs (engine `executorch`, `experimental` flavor only) use the same accelerator
protocol on the CPU: react-native-executorch 0.10 with ExecuTorch's XNNPACK backend, the only
ExecuTorch backend the APK packages. A run names its `.pte` and the `tokenizerFile` staged next
to it. The app hashes both before measuring (`modelSha256`, `tokenizerSha256`); a tokenizer that
differs from its pin is recorded unmeasured (`tokenizer-hash-mismatch`), as a model does.
`nThreads` and `nCtx` are null: the export fixes the context, and the XNNPACK threadpool size is
ExecuTorch's default.

[`ExecutorchAdapter.ts`](../xebenchapp/src/harness/adapters/ExecutorchAdapter.ts) creates the
runner with `createLLMRunner` (ExecuTorch's `TextLLMRunner`) on a dedicated worklet thread and
passes the measured text to `generate()` verbatim: no chat template, no BOS token added (the
runner tokenizes with `num_bos` 0), greedy (temperature 0). With `measuredPrompt: std-pp512-v1`
it receives the text the llama.cpp CPU rows measure. The runner splits a prompt longer than the
program's token input into several prefill forwards (512 tokens per forward for the Gemma 4 E2B
export); each repetition records that bound and the number of forwards
(`executorchStats.prefillChunkTokens`, `prefillChunks`).

Fixed decoding is native: `decodeMode: fixed` passes `ignoreEos` with `maxNewTokens` 128, which
decodes exactly 128 tokens past the end of turn (the prefill samples the first, the decode loop
the other 127). The app refuses a repetition that produced another count. The output gate runs
the same file with the runner's own stopping rule.

The timings (`engine-timings`) are the runner's own statistics (`llm::Stats`, wall-clock
milliseconds), computed as ExecuTorch's own report computes them:

- prefill tok/s = prompt tokens / (first token − inference start): tokenization, every prefill
  forward and the sampling of the first token;
- decode tok/s = decode-loop tokens / (inference end − first token): one forward per token, so
  the token the prefill sampled is not counted.

`decodeTokens` is every token the request produced (128 under fixed decoding). TTFT is timed on
the worklet thread with the steady clock, from just before `generate()` to the first token
callback. `engineInternal` keeps the two intervals and rates and `executorchStats` the raw
statistics; the host validator recomputes the rates from them.

Backend evidence (`program-delegates`): before the timed loads, the app reads the delegates each
method of the `.pte` uses from its metadata, and after each load the backends registered in the
process and the ExecuTorch libraries mapped. A session is `confirmed` on the CPU when every
delegate is `XnnpackBackend`, that backend is registered and no other ExecuTorch backend library
is mapped; a delegate to any other backend is a `mismatch`.

`runtimeVersions` records `executorch` (the `ET_VERSION` of the headers shipped with the linked
native libraries, embedded at build time) and `reactNativeExecutorch` (the package version).

A run field that is not set takes the harness default in parentheses
(`LLAMA_RN_DEFAULTS` in [`LlamaRnAdapter.ts`](../xebenchapp/src/harness/adapters/LlamaRnAdapter.ts)).
With llama.rn 0.13.0-rc.7, `use_mmap: true` selects memory-mapped loading
(`LLAMA_LOAD_MODE_MMAP`), and `use_mmap: false` with `use_mlock: false` reads the weights
into memory (`LLAMA_LOAD_MODE_NONE`; `cpp/jsi/JSIParams.cpp`). `no_extra_bufts: false` lets
llama.cpp repack weights into its extra CPU buffer types; it is also the binding's default.

cpu-reference-v2's four values are PocketPal's Android defaults in
[PocketPal](https://github.com/a-ghorbani/pocketpal-ai) 1.20.0 (commit `07ef06f`).
`createDefaultContextInitParams()` in `src/utils/contextInitParamsVersions.ts` sets
`use_mmap: 'false'` and `flash_attn_type: 'off'` on Android, `use_mlock: false` and
`no_extra_bufts: false`. `ModelStore.getEffectiveContextInitParams()` in
`src/store/ModelStore.ts` passes them to `initLlama()`, with `'false'` resolved to
`use_mmap: false`. Every other setting stays the CPU reference's: `n_gpu_layers` 0,
`n_threads` 4, `n_ctx` 2048, `n_batch` 512, `n_ubatch` 512, f16 K/V caches, greedy sampling,
and the prompt and decode length of cpu-reference-v1. PocketPal's defaults for `n_ctx`,
`n_batch`, `n_ubatch` and the K/V cache types are the same. Its other `initLlama()`
parameters are not adopted, among them `n_gpu_layers: 99` with automatic devices,
`kv_unified: true`, `n_parallel: 1`, `state_cache_budget_mb` and the per-device thread count
it sets on first launch; the harness leaves the ones it does not list to the binding.
For this single-sequence CPU run, `kv_unified`, `n_parallel` and `state_cache_budget_mb`
change nothing. llama.rn's own `n_parallel` default is also 1. With one sequence, the KV
cache has one stream and a 2048-token context per sequence, unified or not. The prompt state
cache only reuses state across turns on recurrent, hybrid or SWA models. Llama 3.2 is none of
these, and every repetition loads a fresh instance.

cpu-reference-v3 keeps PocketPal's load settings (no memory map, no mlock, repacking on) and
sets flash attention to `auto`. With llama.rn 0.13.0-rc.7 and Llama 3.2 1B Q4_K_M on 4 CPU
threads, cpu-reference-v2 prefills 18–31% slower than cpu-reference-v1 on the POCO F8 Ultra,
Galaxy S23 and Pixel 9. On the F8, flash attention off accounts for
all of it, and the load mode changes only the load time
([reference-config-v2](experiments/reference-config-v2.md#cpu-reference-v3)).

Other engine adapters remain experimental; expanding reference coverage is a follow-up in
issue #3.

Build the `reference` flavor, which packages only llama.rn's CPU libraries. It
needs no vendor SDK and no source or packaging edits; prerequisites are listed in
the README's [build flavors](../README.md#build-flavors-and-prerequisites) section.
From `xebenchapp/android`:

```sh
./gradlew assembleReferenceRelease
python3 ../../scripts/check_reference_apk.py app/build/outputs/apk/reference/release/app-reference-release.apk
```

A successful Gradle build alone does not prove that the runtime libraries were
packaged. The check fails if any llama.rn CPU variant or the JS bundle is missing,
or if the APK carries a native library outside React Native core and llama.rn CPU
(for example Hexagon/HTP, OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X or ExecuTorch) or a
LiteRT-LM/GENIE-X/ExecuTorch engine bridge. The Java glue of react-native-executorch and
react-native-worklets remains because autolinking is not flavor-aware. The application ID stays `com.xebenchapp`.

## Build identity and reproducibility

The APK embeds its git commit, a dirty flag, its flavor, build type, `versionName`
and the SHA-256 of the bundled configuration (`BuildConfig`). Every session records
them, plus the SHA-256 of each certificate that signed the installed APK (see
[Evidence](#evidence)). A dirty build cannot be rebuilt from its commit, so treat
`gitDirty: true` as unreproducible.

CI ([`android-build.yml`](../.github/workflows/android-build.yml)) builds
`assembleReferenceRelease` (arm64-v8a) for the pull-request head commit, runs the
APK check and uploads the APK with its SHA-256, the signing certificate and the
embedded build identity. To compare a local build, check out that commit with a
clean tree, build with the same JDK major version, and compare SHA-256s.

**The APK SHA-256 depends on the signing key.** The `release` build type is signed
with the checked-in `android/app/debug.keystore` (certificate SHA-256
`fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`), so anyone can
reproduce the signed bytes. An APK signed with any other key has a different SHA-256
even when its contents are identical. Compare unsigned contents in that case, for
example with `apksigner verify --print-certs` plus a diff of the unzipped entries
other than `META-INF/`. The capture manifest's `appIdentity` records the installed
APK hashes, and the session's `build.signingCertSha256` says which key signed them.
Bit-for-bit reproducibility is measured, not guaranteed: the build is not
hermetic (host NDK/SDK, JDK), so a mismatch is informative, not a failure.

Measured on PR #24 (CI on x86-64 Ubuntu with Temurin 21, against a local aarch64
Linux host with OpenJDK 21, same commit and both clean): the APK SHA-256s differ.
The dex files, the Hermes bundle, `resources.arsc` and every other asset are
identical. The difference is confined to the eight libraries compiled by CMake during
the build (`libappmodules.so`, `libreact_codegen_safeareacontext.so` and llama.rn's
`librnllama_jni*.so`). Seven differ only in their 20-byte GNU build ID, which hashes
host-specific build paths. `libreact_codegen_safeareacontext.so` also embeds the
absolute path of a React Native header in the Gradle cache, which includes the
build user's home directory. Since llama.rn 0.13.0-rc.7 compiles its core from
source, every `librnllama*.so` also embeds the absolute paths of its source files
(about 165 per library, e.g. `<checkout>/xebenchapp/node_modules/llama.rn/cpp/...`),
so a local build discloses its checkout path and user name in six libraries. Do not
distribute an APK built under a home directory you do not want to disclose; share
CI-built APKs (`/home/runner/...`) instead. Release builds set React Native's dev-server IP
resource to `localhost`, so the build host's address is not in the APK. Removing
the path dependence from native builds is a follow-up.

## Configure and capture

Install the app first. Then stage the pinned reference GGUF in the
app's external files directory:

```sh
python3 scripts/stage_model.py --serial <serial>   # default: models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
```

The script refuses a file whose name, size or SHA-256 differs from
[`configs/models/reference-models.json`](../configs/models/reference-models.json).
It also refuses when the pushed copy fails a `sha256sum` check on the device. It
never downloads the model. [MODELS.md](../MODELS.md) gives the license terms and the
download command for the pinned revision.

To customize a run, copy the bundled JSON and edit its model basename, quant
label, threads/context, load settings (`useMmap`, `useMlock`, `noExtraBufts`,
`flashAttnType`), repetition, cooldown, readiness timeout
(`readinessTimeoutSec`) or battery-rise bound (`maxBatteryTempRiseC`) settings, or
add `orderSeed` to reproduce a recorded run order. To run cpu-reference-v1 or
cpu-reference-v3, stage the committed file byte for byte, so the recorded
`configurationSource` hash names it. cpu-reference-v3 also needs its three model files on
the device (`stage_model.py --serial <serial> models/<file>` for each, [MODELS.md](../MODELS.md)),
and one invocation runs 9 repetitions with a 120 s cooldown and up to 900 s of readiness
wait each, so give `capture_app_run.sh` a timeout well above the default 900 s (at least
3600 s; the worst case is longer). A run
may pin `modelSha256` (64 lowercase hex); the bundled configuration pins the reference
GGUF from the manifest. `stage_model.py` stages only models listed in the manifest. Push any other
GGUF with `adb` yourself, and remove or replace its `modelSha256`; runs with it are
not reference runs. Then stage the configuration:

```sh
adb -s <serial> push my-config.json /sdcard/Android/data/com.xebenchapp/files/xebench-config.json
scripts/capture_app_run.sh <serial> 900
```

The staged configuration persists across launches; if absent, the app uses the
bundled default. Unknown fields, unsupported engines, paths/URLs and invalid
limits fail before model loading. A model whose SHA-256 differs from the run's
`modelSha256` is never loaded: the run is saved as a failed `model-hash-mismatch`
session with both hashes, and every later run is saved as `configuration-aborted`.
Each repetition waits for measured [readiness](RUN-CONDITIONS.md) and is sampled
while it runs; overall guards stay
**unverified** unless a failure is known, because backend execution is unverified.
Model hashes identify the bytes; model/quant labels remain operator-supplied.

## Evidence

- Each session records the complete configuration, prompt, model SHA-256/size,
  binding version, native token counters and every attempted repetition.
- Successful repetitions retain load time, TTFT, throughput and timing methods.
  Summaries include median, IQR, min/max and actual sample count. Quartiles use
  linear interpolation at index `(n - 1) * p`.
- A checkpoint is saved after each repetition and before cooldown. Failures
  retain completed repetitions and release the engine before stopping the session.
  Cleanup failure aborts the whole configuration after saving both errors, if any.
  Readiness timeouts are saved as `skipped` repetitions. Runs that are not measured
  after an abort, or whose model cannot be read, are saved as failed sessions with
  a `stopReason`. The executed run order and its seed are recorded.
- Runs without positive timed decode work are failed, not reported as zero speed.
  A one-token limit can hit this case because the first token comes from prefill.
  Decode counters describe timed evaluations, not the total generated output.
- Records of protocol `cpu-reference-v3` (harness `xebench-0.7-reference`) also
  identify the binary, the configuration and what the engine was given:
  - `build`: commit SHA and dirty flag, flavor, build type, `versionName`/`versionCode`
    and the SHA-256 of each APK signing certificate, embedded at build time and
    read through the `BuildInfo` native module. Without the module it is
    `status: "unavailable"` with every field null; unknown values are never guessed.
  - `configurationSource`: `bundled` or `staged`, with the SHA-256 of the file's
    bytes. A staged file is hashed before and after it is read and must not change;
    the bundled file is hashed at build time. A debug build loads its JavaScript,
    including the bundled configuration, from Metro, so its bundled hash is null and
    its `build` describes only the native binary.
  - `effectiveSettings`: every llama.rn `initLlama` parameter (except the model
    path) and `completion` parameter (except the prompt) the harness passes, for
    example `n_batch`, `n_ubatch`, `use_mmap`, `flash_attn_type`, the KV cache types,
    `temperature` and `seed`. Values equal to the binding's defaults are still passed
    explicitly; anything unlisted is the binding default of `engineVersion`. The load
    settings come from the configured run (see the table above); 0.13's own default load
    mode is `AUTO`, which the harness never uses. `distribution` records
    the binding as installed: `{source: npm, package: llama.rn, version, patches: []}`.
    xebench uses the npm package as published, with no patches, so a third party can
    rebuild it from public packages.

    **Not PocketPal's binary.** PocketPal ships llama.rn 0.13.0-rc.7 with its own
    patch (`patches/llama.rn+0.13.0-rc.7.patch`, patch-package, 1034 lines). The patch
    changes the JSI layer (`cpp/jsi/*`), the completion loop (`cpp/rn-completion.*`),
    context abort handling (`cpp/rn-llama.*`, which installs an abort callback that
    ggml checks between graph nodes), multimodal prompt bookkeeping (`cpp/rn-mtmd.hpp`)
    and stop handling in `src/index.ts`. It does not touch the vendored llama.cpp/ggml
    sources. Kernels should therefore match PocketPal's app, but TTFT and decode timing
    may differ slightly.
    cpu-reference-v2 adopts PocketPal's Android load settings (above). PocketPal's
    `n_gpu_layers: 99` with automatic devices and `kv_unified: true` are not adopted.
  - `runtimeVersions`: the versions of the runtimes the engine runs on, taken from the
    shipped artifacts, never typed by hand. For llama.cpp: `llamaCpp`
    (`{buildNumber, commit}` of the vendored llama.cpp, from llama.rn's exported
    `BuildInfo`) and `llamaRn` (the installed package version). For LiteRT-LM:
    `litertLm` and, on NPU runs, `litertNpuRuntimeQualcomm` (the AARs as Gradle
    resolved them for the APK) and `qairt` (the build string embedded in the packaged
    `libQnnHtp.so`). For GENIE-X: `genie` (the geniex-android AAR version). Runtimes the engine does not use, and
    values that are missing or malformed, are null.
  - `executionReports`: one per successful model load, with what llama.rn reported
    about that context: `gpu`, `reasonNoGPU`, `devices`, the loaded native library
    variant (`androidLib`) and llama.cpp's `systemInfo` line (threads in use and
    compiled CPU features), plus `nativeLibraries`: each `librnllama*.so` mapped in
    the process with its ELF GNU build ID, read by the BuildInfo module. llama.rn
    0.13.0-rc.7 compiles its native core from source in every app, so the build ID,
    not the npm version, names the binary. Values that are missing or out of bounds
    are null.
    These are engine statements, not verified execution, so `observedBackend`
    stays null.
  - `artifact.expectedSha256`: the run's configured `modelSha256`, or null.
- Files live under `xebench-results/` in the app's external directory. Logcat
  carries short filename/checksum references; host capture verifies and retrieves
  them without changing their bytes. Local manifests distinguish checkpoints from
  terminal records and carry host-collected device facts separately.
- Capture fingerprints installed base/split APKs before launch and after completion.
  The manifest's `appIdentity` reports matched, changed or unverified snapshots;
  paths are omitted. A changed APK returns exit 4 while preserving evidence.
  Missing probes and interrupted runs cannot establish a match. Matching snapshots
  identify installed bytes, not source provenance or continuous runtime attestation.

“Cold” means a fresh engine instance in a warm process. Hashing the model before
measurement warms the OS file cache. Actual backend execution, peak memory and
quality remain unverified; engine reports and build identity are recorded as
reported, not attested. Condition sampling is 1 Hz, not continuous.

Completed sessions can be previewed from raw records with `aggregate.mjs --pretty`.
They are published only from their sanitized [evidence export](#evidence-export):
`aggregate.mjs --from-bundles evidence/ --dry-run` reports what each hosted bundle would
publish, and `--update --session <id>` publishes a named session under
[publication policy v1](RUN-CONDITIONS.md#publication-policy-v1) (README,
"Results are published here"). Raw records and diagnostics stay local; only a sanitized
evidence export may leave the machine. Historical data is unchanged.

## Host validation

Capture validates every `schemaVersion: 1` record, checkpoints included, against
[`raw-session-v1.json`](../schemas/raw-session-v1.json) plus cross-field rules in
[`raw_session.py`](../scripts/raw_session.py). It rejects malformed, truncated or
non-finite JSON, unknown versions and fields, a repetition count or `runIndex`
sequence that disagrees with `nColdRuns`, and inconsistent status. `running` has
fewer than the configured attempts, all successful; `complete` has all of them;
`failed` ends at its only failed attempt. Summaries are recomputed from successful
repetitions with the interpolation above. The record must also agree with its
configured run, and failed condition checks require `guardsPassed=false`.
Per-attempt conditions, `conditionPolicy`, `protocol` and `configuration` accept
additional fields. Records from apps built before condition snapshots omit all
condition evidence and keep `guardsPassed=null`; a mix is rejected.

`protocol.version` selects the schema branch. `cpu-reference-v1` records keep the
rules above unchanged (frozen fixture `tests/fixtures/raw-session/app-emitted-v1.json`).
`cpu-reference-v2` records (harness `xebench-0.6-reference`) must also carry
readiness, sampling, instance, order and verdict evidence. For these, the validator:

- checks `status` against `stopReason`: a skipped repetition ends a
  `readiness-timeout` session, and unmeasured runs have no attempts and
  `artifact: null`;
- requires the published `run-conditions-v2` values and `charging-policy-v3` (or the
  earlier status-only `charging-policy-v2`), and uses the configured
  `maxBatteryTempRiseC`;
- types and ranges every snapshot and sample field as the app does;
- recomputes the monitor summary from persisted samples, or bounds it when the
  samples were decimated;
- requires exactly the guard notes implied by the verdict;
- recomputes every endpoint, readiness and sampling assessment, the condition
  verdict and its charging status from the recorded snapshots and monitor summaries,
  so unknown evidence cannot be recorded as ready or passing;
- reproduces the seeded run order.

`cpu-reference-v3` records (harness `xebench-0.7-reference`; earlier v2 output is
frozen in `tests/fixtures/raw-session/app-emitted-v2.json`) follow every v2 rule
and also require `build`, `configurationSource`, `effectiveSettings`,
`executionReports` and `artifact.expectedSha256`, each with a closed shape. The
validator also checks that:

- `expectedSha256` equals the configured `modelSha256` (or is null without one),
  and that `stopReason` is `model-hash-mismatch` exactly when the hashed model
  differs. Such a run has no attempts but keeps its artifact;
- the effective `n_ctx`, `n_threads` and `n_predict` agree with the protocol, and
  `n_gpu_layers` is 0;
- the effective `use_mmap`, `use_mlock`, `no_extra_bufts` and `flash_attn_type` equal the
  configured run's load settings, or the harness defaults for those it does not set.
  Records from builds that did not pass `no_extra_bufts` omit it; that is accepted only
  for a run that does not set `noExtraBufts`;
- every successful load has exactly one execution report, in repetition order, and
  a failed load has none. A report of GPU devices is kept as evidence, not
  rejected: in the `experimental` flavor llama.rn registers OpenCL even with
  `n_gpu_layers` 0;
- a staged configuration carries its hash, a bundled hash comes only from a
  reported release build, an unavailable build reports no other field, and a
  `versionName` flavor suffix matches `flavor`.

A rejected payload stays in `invalid-<id>.txt` beside `invalid-<id>.reason.json`
(reason codes, JSON Pointer paths, messages); the manifest lists `rejections`.
A retrieved device file is stored as retrieved, with no added newline. Its reason
file keeps the `sourceFile` and `claimedSha256` from the logcat reference, so the
device checksum can be re-checked from saved evidence. This holds for UTF-8 records
without carriage returns, because adb output is read as text. An oversized file
keeps only its bounded prefix.
A rejection prevents a `complete` capture: after `XEBENCH_DONE` it is `partial`.
Exit codes: 0 complete, 2 setup, startup, capture or cleanup failure, 3 timeout,
4 partial or app-changed, 130/143 interrupted. Manifest entries carry `validation`:
`raw-session-v1`, or `legacy-unvalidated` for inline records without
`schemaVersion`; file references must be v1. Validation is not
comparison eligibility; `publicationReady` stays false. To check files offline, run
`python3 scripts/raw_session.py <file>...`. After an intended record change, run
`XEBENCH_UPDATE_FIXTURES=1 npx jest __tests__/rawSessionSchema.test.ts` in
`xebenchapp` and update the schema; CI fails while the two disagree.

## Evidence export

Capture directories are local diagnostics. Nothing in them is public until it is
exported. [`export_evidence.py`](../scripts/export_evidence.py) is the only path
from a capture to a shareable artifact:

```sh
python3 scripts/export_evidence.py export --out results/exports results/<captureId>...
python3 scripts/export_evidence.py verify results/exports/<bundleSha256>
```

Export fails closed (exit 1, nothing written) if any check fails. It checks every
listed record and checkpoint against its capture checksum and re-validates it with
`raw_session.py`. It refuses legacy records, unlisted record files, inconsistent
counts, duplicate sessions and captures without a terminal record. It also refuses
an approved field whose value does not have the expected form. A final screen
refuses an export if any output string looks like an email, local path, token,
ANDROID_ID-like hex or network serial. Refusal messages name inputs by position
and JSON location, never by value. A complete capture must have no errors,
rejections or failures.

Only allowlisted fields are copied. Unknown fields are dropped and listed in the
report on stdout, which stays local. Reason codes, rejection codes, signal names,
guard notes, enumerated values (statuses, methods, phases, modes) and the cold-run
definition come from a closed app/host vocabulary. Other values become `unrecognized`.

- **Exported:** session and configuration identity, protocol with `promptSha256`
  in place of the prompt text, engine and version, model SHA-256 and the configured
  expectation, the configuration source and its hash, the effective engine settings,
  build identity (commit, which links bundles built from one unpublished commit;
  dirty flag, flavor, build type, a version name of the
  form `1.0-reference`, version code, and `signingKey`: `repository-debug-key` or
  `other`), execution reports (GPU flag, and llama.rn's no-GPU reason, device names
  and library variant from closed lists; `systemInfo` only as parsed thread counts
  and known feature flags), every repetition
  (metrics, token counts, methods, failure phase), condition snapshots including
  battery temperature, policies, verdicts and summaries. Device identity is
  limited to manufacturer, model, SoC and Android release. A capture contributes
  its status, counts, rejection reason-code counts, and APK names with their SHA-256.
- **Never exported:** serials, ANDROID_ID, the `ro.product.device` codename,
  install and host paths, usernames, logcat, error logs, error and cleanup
  messages, invalid payloads and their reason files, signing-certificate
  fingerprints, the raw `systemInfo` line, and `publicationReady`.

The bundle `<bundleSha256>/` holds `manifest.json`, `captures/<captureId>.json` and
`sessions/<runId>.json`. Every file is canonical JSON: sorted keys, `,`/`:`
separators, ASCII only, no NaN or Infinity, and one trailing LF. The files contain
no export time, host or path, so the same captures always give the same bytes.
**`bundleSha256` is the SHA-256 of the bytes of `manifest.json`.** The manifest
lists every other file with its SHA-256 and size. The export prints
`{sessionIds, bundleSha256}`, plus `url` when `--base-url https://...` is given,
for a verified row's `evidence`. To check a bundle, hash the bytes as served;
never re-serialize them.

Public bundles are committed byte for byte under
[`evidence/<bundleSha256>/`](../evidence/README.md) and served from
`https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence/<bundleSha256>/manifest.json`.
Exporting with `--base-url https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence`
prints that `url`. A committed bundle is never edited; a new export is a new
directory. Git does not normalise these files, and CI checks that each directory
name equals the SHA-256 of its `manifest.json` and that `verify` passes.

`verify` checks the following:

- the hash, every checksum and size, and any unlisted or symlinked files;
- canonical form, and allowlist conformance of the manifest and every file;
- the privacy screen;
- that each file name is bound to its ID;
- repetition numbering and count against `nColdRuns`, and status against the repetitions;
- that capture counts agree with the sessions;
- every summary, recomputed from the repetitions with the interpolation above.

With `--session ID`, it also checks that a row's sessions are in the bundle. A
malformed bundle produces errors and exit 1, never a crash. `verify` does not
re-run the full raw-schema validation, because the raw record is not exported.

Approved labels (model, quant, engine version, prompt label, policy versions,
configuration and run IDs) and device properties are copied verbatim once they pass the form checks and the screen. A serial typed
into a label cannot be detected. Exporting a session does not make it eligible
for publication: publication policy v1 decides that from the bundle.
