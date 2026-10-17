# LiteRT-LM on CPU, GPU and NPU next to llama.cpp (accelerator-v1)

Part of [#9](https://github.com/a-ghorbani/xebench/issues/9). Status: **harness and configurations
ready; device runs pending.** The results section below is a template until the runs are done.

## Question

How fast is Gemma 3 1B on LiteRT-LM's CPU, GPU (ML Drift) and Qualcomm NPU paths, measured from
an installed app on stock Android, and how does that compare with llama.cpp (llama.rn) on the
same phones? Every GPU/NPU number must come with evidence that the output was correct, because
one published NPU bundle reports fast tok/s while producing wrong text
([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)).

| Phone | SoC | LiteRT-LM | llama.cpp (llama.rn) |
|---|---|---|---|
| POCO F8 Ultra | SM8850 (Hexagon v81) | CPU, GPU, NPU | CPU, OpenCL, Hexagon |
| Galaxy S23 | SM8550 (Hexagon v73) | CPU, GPU (+ NPU as bug reproduction only) | CPU, OpenCL |

## What is and is not the same

The two engines run **different artifacts of the same model**. Rows are labelled by artifact and
must never be read as an identical-model comparison.

| Engine | Artifact | Quantization | Used for |
|---|---|---|---|
| LiteRT-LM | `gemma3-1b-it-int4.litertlm` | int4 (LiteRT) | CPU, GPU |
| LiteRT-LM | `Gemma3-1B-IT_q4_ekv1280_sm8850.litertlm` | q4, KV 1280, AOT-compiled for SM8850 | F8 NPU |
| LiteRT-LM | `Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm` | q4, KV 1280, AOT-compiled for SM8550 (QAIRT 2.34) | S23 NPU, bug reproduction only |
| llama.cpp | `gemma-3-1b-it-q4_0.gguf` | GGUF Q4_0 | CPU, OpenCL, Hexagon |

Every session records the SHA-256 and size of its artifact, so a reader can identify the exact
file. The NPU bundle is a different file from the CPU/GPU artifact; its correctness reference is
the CPU run of the int4 artifact (`sameArtifact: false` in the record).

Held equal: the request text (one ~430-word user message asking for a three-sentence summary),
greedy sampling, a 128-token decode budget, 4 CPU threads on the CPU runs, the run conditions and
the repetition protocol. Each engine applies its own artifact's chat template: LiteRT-LM through
`Conversation` (the `.litertlm` template), llama.cpp through the identical Gemma 3 user turn
built by the harness (`prompts.ts gemma3Turn`, BOS added by the tokenizer). Both engines report
their own prompt token counts, so a reader can check that the inputs match.

## Build

The `experimental` flavor carries LiteRT. Everything LiteRT/QAIRT lives in its source set
(`app/src/experimental`); the `reference` flavor rejects any of it (`scripts/check_reference_apk.py`).

| Piece | Version | Source |
|---|---|---|
| `com.google.ai.edge.litertlm:litertlm-android` | 0.18.0 | Google Maven (`experimentalImplementation`) |
| `com.google.ai.edge.litert:litert-npu-runtime-qualcomm` (dispatch + compiler plugin) | 2.3.0 | Google Maven (`experimentalImplementation`) |
| QAIRT QNN runtime: `libQnnHtp`, `libQnnSystem`, `libQnnHtpV73/V81Stub`, `libQnnHtpV73/V81Skel` | 2.50.0.260828 | Qualcomm (proprietary, staged locally, never committed) |
| Manifest `<uses-native-library>` (`required="false"`) | `libcdsprpc.so`, `libOpenCL.so`, `libvndksupport.so` | `app/src/experimental/AndroidManifest.xml` |

The dispatch library and QAIRT must match the LiteRT-LM release's `LITERT_REF`. A mismatch logs
`Unsupported dispatch runtime version` and engine creation fails. Steps:

```sh
python3 scripts/stage_qairt_libs.py --qairt ~/qairt/2.50.0.260828   # git-ignored jniLibs + qairt-libs.json
cd xebenchapp/android && ./gradlew clean assembleExperimentalRelease
python3 ../../scripts/check_reference_apk.py --flavor experimental --require-qnn \
  app/build/outputs/apk/experimental/release/app-experimental-release.apk
```

`stage_qairt_libs.py --check` verifies an existing staging against the SDK. The SHA-256 of every
staged QNN library is printed and written to `app/src/experimental/jniLibs/qairt-libs.json`; the
results section records them with the APK hash.

## Protocol: accelerator-v1

`accelerator-v1` reuses `cpu-reference-v2` unchanged for repetitions and conditions
([RUN-CONDITIONS.md](../RUN-CONDITIONS.md)): readiness-gated fresh-instance repetitions (load →
first request → release in the foreground app process), a fixed cooldown before every
repetition, 1 Hz condition sampling during load and inference, `charging-policy-v3`
(tethered, eligible only while not charging), a seeded run order (`mulberry32-fisher-yates-v1`,
seed fixed in the configuration) and a recomputable condition verdict. It adds three things.

### 1. Engine-reported timings for both engines

- **llama.cpp:** llama.rn `completion()` on the raw Gemma 3 turn; prefill and decode tok/s from
  llama.cpp's own timings (`engine-timings`), TTFT from the JS token callback.
- **LiteRT-LM:** `Engine` + `Conversation` with `ExperimentalFlags.enableBenchmark`; prefill and
  decode tok/s from `Conversation.getBenchmarkInfo()` (`engine-timings`), TTFT from request to
  first streamed callback on the native worker thread. `loadMs` is `Engine` creation plus
  `initialize()`. The earlier synthetic `benchmark()` call is no longer used, so both engines
  process the same real prompt.

### 2. Observed-backend evidence

A requested backend is not proof of where inference ran. After each load the record stores what
the engine or process reported, and a status derived by fixed rules (`backend.ts`, recomputed by
`scripts/raw_session.py`):

| Status | Meaning |
|---|---|
| `confirmed` | The engine reported executing on the requested backend. llama.rn reports its context `gpu` flag and used devices (`HTP0` for Hexagon). For a CPU run, `gpu: false`. |
| `consistent` | Indirect evidence only. LiteRT-LM does not report its executing backend, so the record lists the accelerator libraries mapped in the process before and after engine creation (`/proc/self/maps`). NPU and GPU count only when that engine creation **newly** loaded `libLiteRtDispatch_Qualcomm.so` or `libOpenCL.so`; CPU counts when it loaded neither. A loaded runtime is not proof of execution. Also used for a llama.cpp CPU run (`nGpuLayers` 0) that reports `gpu: true`: on a build with OpenCL/Hexagon, llama.rn attaches the GPU devices to the model even when no layer is offloaded (seen on the F8 Ultra: `GPUOpenCL`, `HTP0`–`HTP5`), and neither backend implements op offload, so nothing indicates GPU execution but the report cannot prove CPU-only either. |
| `mismatch` | The engine reported a different backend (for example llama.rn `gpu: false` on an OpenCL run). |
| `unknown` | No usable evidence. Unknown stays unknown. |

The session's `observedBackend` is the weakest status across its repetitions (any mismatch wins).
`gateBackend` holds the same evidence from the gate's load, which is the first load of that run in
the process. For LiteRT-LM it is the only load that can show the runtime being loaded: by the
measured repetitions the library is already mapped, so their status is `unknown` by design. If an
earlier run already mapped the library (for example llama.rn's OpenCL variant mapping
`libOpenCL.so`), even `gateBackend` is `unknown` for LiteRT GPU. The record lists the libraries,
so a reader sees why. Sessions keep the note "Actual backend execution has not been verified"
unless every repetition is `confirmed`, so LiteRT rows always carry it. An engine-reported
`mismatch` (in the repetitions or the gate) makes the session invalid for comparison.

### 3. Output-correctness gate (output-gate-v1)

Before a configuration's measurements, every CPU run answers a fixed greedy probe set; these are
the references. Just before each GPU/NPU run is measured, the same engine and backend answer the
same probes on a fresh instance. The probes (`gate.ts`):

| Probe | Prompt | Check |
|---|---|---|
| `short-capital` | "What is the capital of France? Answer with one word." | contains `paris` |
| `long-fact-last` | ~230 tokens of office filler, then the code word PELICAN, then the question | contains `pelican` |
| `long-fact-first` | the code word first, then the filler and question | contains `pelican` |
| `long-fact-middle` | filler, "room 417", filler again (~480 tokens), question | contains `417` |
| `measured-prompt` | the measured prompt at the measured decode budget | agreement only (exact match and shared prefix with the CPU reference) |

The first three are verbatim from the 2026-10-17 spike. Three answer probes exceed one 128-token
prefill chunk, with the fact last, first and in the middle. Answers are matched as whole words
after lowercasing and collapsing punctuation.

Verdicts (recomputed by the host validator from the recorded outputs):

- A reference (CPU) gate **passes** if every answer probe contains its keyword.
- A candidate (GPU/NPU) gate **passes** if its reference passed and every answer probe passes;
  **fails** on any wrong answer to a probe the reference got right, even if a later probe errored;
  otherwise it is **unverified** if its gate errored or the reference gate did not run or did not
  pass.
- `comparison.status` is **invalid** for a failed gate, a `bug-repro` run or an engine-reported
  backend `mismatch`, **unverified** for an unverified gate, otherwise **valid**. An invalid
  session keeps all its metrics; it is only excluded from comparisons.
- The probe set is pinned: the host validator rejects a record whose probes are not exactly
  output-gate-v1 (IDs, prompt SHA-256, keywords, budgets, order), and `verify` checks that a
  candidate's copied reference outputs and status match the reference session in the same capture.

The gate runs before measurement on its own engine instance, so it also warms OS file and engine
caches (GPU program caches, NPU context). All measured repetitions therefore see warm caches; the
cold definition in each record says so.

### Records, validation and export

Sessions are `xebench-raw` schemaVersion 1 records on the new `sessionV3` branch of
`schemas/raw-session-v1.json` (harness `xebench-0.7-accelerator`, protocol `accelerator-v1`),
configured by schemaVersion 2 configurations (`config.ts parseAcceleratorConfig`). The change is
additive: `cpu-reference-v1/v2` records validate as before. `scripts/raw_session.py` additionally
checks the configuration rules, the record against its run, every backend status, every probe
verdict, the correctness and comparison status and `sameArtifact`.
`scripts/export_evidence.py` exports the new fields. Probe prompts, model outputs and llama.rn's
free-text `reasonNoGPU` stay in local raw evidence; the bundle carries their SHA-256 instead, and
`verify` re-derives the correctness and comparison status from the exported probe verdicts.
The drift fixture `tests/fixtures/raw-session/app-emitted-accelerator.json` is generated by
`xebenchapp/__tests__/accelerator.test.ts`.

### Configurations

| File | Runs | Repetitions | Order seed |
|---|---|---|---|
| `xebenchapp/configs/f8-gemma3-accelerators-v1.json` | LiteRT cpu, gpu, npu (sm8850); llama.cpp cpu, opencl, hexagon | 10 | 20261017 |
| `xebenchapp/configs/s23-gemma3-accelerators-v1.json` | LiteRT cpu, gpu; llama.cpp cpu, opencl | 10 | 20261018 |
| `xebenchapp/configs/s23-litert-npu-bugrepro-v1.json` | LiteRT cpu (reference), npu sm8550 (`bug-repro`) | 5 | 20261019 |

The S23 NPU run is in its own configuration so that a native NPU failure cannot cost the main S23
matrix, and its `bug-repro` purpose makes every one of its sessions invalid for comparison by
construction. Cooldown 30 s, readiness timeout 300 s, battery-rise bound 2 °C, decode budget 128.

Run a configuration:

```sh
D=/sdcard/Android/data/com.xebenchapp/files
adb -s <serial> install -r app-experimental-release.apk
# The GGUF comparator is pinned (MODELS.md); staging verifies the hash on host and device.
python3 scripts/stage_model.py --manifest configs/models/experimental-models.json --serial <serial> <gguf>
# xebench-config.json is shared with other experiments: back it up, restore it afterwards.
adb -s <serial> shell "[ -f $D/xebench-config.json ] && cp $D/xebench-config.json $D/xebench-config.json.litert-backup"
adb -s <serial> push xebenchapp/configs/f8-gemma3-accelerators-v1.json $D/xebench-config.json
python3 scripts/capture_app_run.py <serial> 7200
adb -s <serial> shell "if [ -f $D/xebench-config.json.litert-backup ]; then mv $D/xebench-config.json.litert-backup $D/xebench-config.json; else rm $D/xebench-config.json; fi"
python3 scripts/export_evidence.py export --out <dir> <capture dir>
python3 scripts/export_evidence.py verify <bundle dir>
```

The LiteRT `.litertlm` artifacts are not pinned (MODELS.md). Their SHA-256 values come from
the session records and are listed with the results.

## Results (template; filled after the device runs)

Medians with IQR (linear interpolation at (n−1)·p) over successful repetitions. `n` is
successful/planned. Correctness and comparison come from the session record. Conditions is the
recorded condition verdict and charging eligibility.

### POCO F8 Ultra (SM8850)

| Engine | Backend | Artifact | Prefill tok/s | Decode tok/s | TTFT ms | Load ms | n | Observed backend | Correctness | Comparison | Conditions | Bundle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LiteRT-LM | CPU | int4 litertlm | | | | | /10 | | | | | |
| LiteRT-LM | GPU | int4 litertlm | | | | | /10 | | | | | |
| LiteRT-LM | NPU | sm8850 q4 ekv1280 | | | | | /10 | | | | | |
| llama.cpp | CPU | Q4_0 GGUF | | | | | /10 | | | | | |
| llama.cpp | OpenCL | Q4_0 GGUF | | | | | /10 | | | | | |
| llama.cpp | Hexagon | Q4_0 GGUF | | | | | /10 | | | | | |

### Galaxy S23 (SM8550)

| Engine | Backend | Artifact | Prefill tok/s | Decode tok/s | TTFT ms | Load ms | n | Observed backend | Correctness | Comparison | Conditions | Bundle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LiteRT-LM | CPU | int4 litertlm | | | | | /10 | | | | | |
| LiteRT-LM | GPU | int4 litertlm | | | | | /10 | | | | | |
| llama.cpp | CPU | Q4_0 GGUF | | | | | /10 | | | | | |
| llama.cpp | OpenCL | Q4_0 GGUF | | | | | /10 | | | | | |
| LiteRT-LM | NPU (bug reproduction, invalid) | sm8550 q4 ekv1280 | | | | | /5 | | | invalid (bug-repro) | | |

Build identity to record with the results: APK SHA-256, `qairt-libs.json` hashes, artifact
SHA-256 per model file, evidence bundle SHA-256 per capture.

## Limitations and threats to validity

- **Different artifacts.** int4 `.litertlm`, AOT q4 NPU bundles and GGUF Q4_0 are different
  quantizations and files of Gemma 3 1B. Throughput differences mix engine, backend and
  quantization effects.
- **Backend evidence for LiteRT is indirect.** `consistent` means the runtime was loaded and the
  engine accepted the backend, not that every op ran there. Parts of the pipeline (sampling,
  embedding lookup) may run on CPU on any backend.
- **Warm caches.** The gate warms engine caches before measurement; first-ever load cost (GPU
  kernel compilation, NPU context setup) is not measured.
- **TTFT measurement points differ slightly:** JS callback for llama.rn, native worker thread for
  LiteRT-LM. Prefill/decode tok/s are engine-internal for both.
- **Decode length depends on the output.** Generation stops at end of turn; decode tok/s is over
  the tokens actually produced (≤128), recorded per repetition.
- **One process per configuration.** Runs share a process (`processRestartBetweenRepetitions:
  false`); accelerator runtimes loaded by an earlier run (for example OpenCL) stay mapped, which
  is why LiteRT evidence counts only libraries newly loaded by an engine creation, and why it is
  usually `unknown` after the first load. LiteRT NPU sets `ADSP_LIBRARY_PATH` with
  `nativeLibraryDir` first and keeps llama.rn's HTP directory, but the two Hexagon stacks
  (QNN and ggml-hexagon) still share one process and one FastRPC client.
- **S23 llama.cpp OpenCL.** llama.rn's combined `hexagon_opencl` library has hung at first model
  load on the S23 in an earlier build (README). A smoke run with one repetition precedes the full
  S23 matrix; if it hangs, the S23 llama.cpp rows come from a build with
  `-Pxebench.excludeLlamaHexagon=true`, where an OpenCL run reports `gpu: false` and is therefore
  recorded as a backend `mismatch` and invalid, not as an OpenCL row.
- **The correctness gate is a smoke test,** not a quality evaluation: four keyword probes and one
  agreement probe. It catches the multi-chunk prefill failure of LiteRT-LM#3508; it cannot show
  that outputs are numerically equivalent to CPU.
- **Two phones, one sample each.** No claim generalizes to other units of the same model or to
  other SoCs.
