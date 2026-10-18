# LiteRT-LM on CPU, GPU and NPU next to llama.cpp (accelerator-v1)

Part of [#9](https://github.com/a-ghorbani/xebench/issues/9). Status: **POCO F8 Ultra (2026-10-17/18) and Galaxy S23 (2026-10-18) measured.** llama.cpp rows: see the llama.rn 0.13.0-rc.7 re-check, which supersedes the 0.12.4 rows for comparison with what PocketPal ships.

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
| `xebenchapp/configs/f8-gemma3-litert-cpu-rerun-v1.json` | LiteRT cpu only, readiness timeout 900 s (rerun after a charging skip, see results) | 10 | 20261020 |
| `xebenchapp/configs/f8-gemma3-llamacpp-rc7-v1.json` | llama.cpp cpu, opencl, hexagon (rc.7 re-check), readiness timeout 900 s | 10 | 20261022 |
| `xebenchapp/configs/f8-llama32-hexagon-check-v1.json` | llama.cpp cpu + hexagon on Llama 3.2 1B Q4_K_M (pinned) and Q4_0 (legacy row), `chatTemplate: llama32` | 4 | 20261021 |
| `xebenchapp/configs/s23-gemma3-accelerators-v1.json` | LiteRT cpu, gpu; llama.cpp cpu, opencl | 10 | 20261018 |
| `xebenchapp/configs/s23-litert-npu-bugrepro-v1.json` | LiteRT cpu (reference), npu sm8550 (`bug-repro`) | 5 | 20261019 |

The S23 NPU run is in its own configuration so that a native NPU failure cannot cost the main S23
matrix, and its `bug-repro` purpose makes every one of its sessions invalid for comparison by
construction. Cooldown 30 s, readiness timeout 300 s (900 s for the S23 configurations and the reruns), battery-rise bound 2 °C, decode budget 128.

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

## Results

Medians with IQR (linear interpolation at (n−1)·p) over successful repetitions. `n` is
successful/planned. Correctness and comparison come from the session record. Conditions is the
recorded condition verdict and charging eligibility.

### POCO F8 Ultra (SM8850), measured 2026-10-17

Medians, IQR in parentheses, over successful repetitions; `n` is successful/planned. Every row ran
the same prompt (447 prompt tokens on LiteRT-LM, 448 on llama.cpp: the BOS difference), greedy,
up to 128 decode tokens. Conditions: run-conditions-v2 verdict and charging-policy-v3 eligibility.

| Engine | Backend | Artifact | Prefill tok/s | Decode tok/s | TTFT ms | Load ms | n | Observed backend (gate) | Correctness | Comparison | Conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LiteRT-LM | CPU (4 threads) | int4 `.litertlm` | 675.9 (12.7) | 47.3 (0.3) | 723 (11) | 323 (7) | 10/10 | consistent (consistent) | pass (reference) | valid | pass, charging satisfied |
| LiteRT-LM | GPU | int4 `.litertlm` | 1976.3 (29.2) | 41.1 (0.1) | 655 (6) | 2301 (11) | 10/10 | unknown (unknown) | pass | valid | pass, charging satisfied |
| LiteRT-LM | NPU | sm8850 q4 ekv1280 bundle | 5320.0 (125.6) | 65.0 (0.6) | 145 (5) | 399 (7) | 10/10 | unknown (consistent) | pass | valid | pass, charging satisfied |
| llama.cpp | CPU (4 threads) | Q4_0 GGUF (QAT) | 394.9 (7.4) | 50.6 (0.9) | 1140 (22) | 551 (18) | 10/10 | consistent (consistent) | pass (reference) | valid | pass, charging satisfied |
| llama.cpp | OpenCL | Q4_0 GGUF (QAT) | 342.9 (1.6) | 27.9 (0.1) | 1316 (9) | 1476 (41) | 10/10 | confirmed (confirmed) | pass | valid | pass, charging satisfied |
| llama.cpp | Hexagon (`HTP0`) | Q4_0 GGUF (QAT) | 256.5 (0.2) | 71.0 (1.4) | 1761 (4) | 552 (5) | 10/10 | confirmed (confirmed) | **fail** (answer-mismatch) | **invalid** | pass, charging satisfied |

What the rows show, within their limits:

- **LiteRT-LM NPU** is the fastest path measured here on both prefill (5320 tok/s, about 8× LiteRT
  CPU) and decode (65 tok/s), and its output passed every gate probe, including the three that span
  several 128-token prefill chunks. On this bundle (SM8850) there is no sign of LiteRT-LM#3508.
- **LiteRT-LM GPU** prefills about 3× faster than LiteRT CPU but decodes slightly slower (41.1 vs
  47.3 tok/s), with the longest load (about 2.3 s, warm GPU caches).
- **llama.cpp Hexagon produced garbage** on every probe (a repeated two-character token) while
  reporting 71 tok/s decode. The engine confirmed it ran on `HTP0`. The same output appeared in a
  separate 1-repetition run with only llama.cpp in the process (no LiteRT-LM loaded), so it is not
  caused by LiteRT's QNN stack sharing the process. Its metrics are kept and marked invalid; it is
  the reason the gate exists.
- **llama.cpp OpenCL** is slower than llama.cpp CPU on this phone for this model.

Notes specific to these rows:

- **LiteRT-LM CPU comes from a second invocation.** In the main invocation
  (`f8-gemma3-accelerators-v1`) its only repetition was skipped: the phone sat at 100 % in
  `BATTERY_STATUS_CHARGING` (top-off charging after the preceding runs) for the whole 300 s
  readiness window, which charging-policy-v3 never measures through. The row above is
  `f8-gemma3-litert-cpu-rerun-v1` (the same run, readiness timeout 900 s), alone in a fresh
  process. Its correctness reference role is the same; the GPU/NPU candidates in the main
  invocation were gated against the main invocation's LiteRT CPU gate, which passed. In the
  1-repetition smoke run, LiteRT CPU in a process that had run llama.cpp first prefilled at
  521 tok/s against 676 here; process history matters for CPU numbers and is not controlled here.
- **Observed backend for LiteRT GPU is `unknown` by design:** llama.rn's OpenCL variant had already
  mapped `libOpenCL.so` before any LiteRT engine was created, so no LiteRT load could show it being
  loaded. The NPU gate saw `libLiteRtDispatch_Qualcomm.so`, `libQnnHtp.so`, `libQnnHtpV81Stub.so`
  and `libQnnSystem.so` newly loaded (`consistent`); the measured repetitions found them already
  mapped (`unknown`).
- **llama.cpp CPU is `consistent`, not `confirmed`:** llama.rn attached `GPUOpenCL` to the model at
  `nGpuLayers` 0 and reported `gpu: true` (see the backend table above).

Build and evidence identity:

| Item | SHA-256 |
|---|---|
| APK (`1.0-experimental`, commit `501bd03`, incremental rebuild of a fresh clean build) | `3954b3874f508eebebbb9dac79b7921b77f1c9926345aef1f2ab450732513088` |
| `gemma3-1b-it-int4.litertlm` | `1325ae366d31950f137c9c357b9fa89448b176d76998180c08ceaca78bba98be` |
| `Gemma3-1B-IT_q4_ekv1280_sm8850.litertlm` | `fda5dca0e8c1c6f65ca5625c326ff79920c7eb82625a0c6515ae4f5711957b1f` |
| `gemma-3-1b-it-q4_0.gguf` | `95e5b8d891cd6a794f66c2a6fb59a41e9562b4660560b854274eceffb628b22a` |
| QAIRT `libQnnHtp.so` / `libQnnSystem.so` | `09e930b6…f264` / `b30bd988…2027` (all six in `qairt-libs.json`) |
| Evidence bundle (both F8 captures, 7 sessions, verified) | `c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972` |

The 1-repetition smoke runs used the earlier APK `070d0d19…dfc1`, whose only difference is that
it recorded llama.cpp CPU with GPU devices attached as `mismatch`; they are not part of the results.

### Galaxy S23 (SM8550), measured 2026-10-18

Same protocol and prompt as the F8 Ultra table. The build is the llama.rn 0.13.0-rc.7 APK
(see the re-check section below). LiteRT-LM does not depend on llama.rn.

| Engine | Backend | Artifact | Prefill tok/s | Decode tok/s | TTFT ms | Load ms | n | Observed backend (gate) | Correctness | Comparison | Conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LiteRT-LM | CPU (4 threads) | int4 `.litertlm` | 229.8 (1.9) | 37.4 (0.3) | 2015 (17) | 322 (5) | 10/10 | consistent (consistent) | pass (reference) | valid | pass, charging satisfied |
| LiteRT-LM | GPU | int4 `.litertlm` | 1158.1 (6.0) | 31.0 (0.1) | 908 (6) | 3470 (27) | 10/10 | unknown (unknown) | pass | valid | pass, charging satisfied |
| llama.cpp 0.13.0-rc.7 | CPU (4 threads) | Q4_0 GGUF (QAT) | 253.1 (0.9) | 22.6 (0.1) | 1818 (35) | 703 (69) | 10/10 | consistent (consistent) | pass (reference) | valid | pass, charging satisfied |
| llama.cpp 0.13.0-rc.7 | OpenCL | Q4_0 GGUF (QAT) | 64.6 (0.1) | 22.8 (0.1) | 6982 (15) | 3169 (286) | 10/10 | confirmed (confirmed) | pass | valid | pass, charging satisfied |
| LiteRT-LM | NPU, **bug reproduction** | sm8550 q4 ekv1280 bundle | 3320.1 (26.5) | 63.0 (0.5) | 190 (4) | 388 (7) | 5/5 | unknown (consistent) | **fail** (answer-mismatch) | **invalid** (bug-repro, correctness-fail) | pass, charging satisfied |

The bug-reproduction configuration (`s23-litert-npu-bugrepro-v1`) has its own LiteRT CPU
reference: 233.9 (2.8) prefill, 38.0 (0.0) decode, 5/5, gate pass.

- **LiteRT-LM#3508 reproduces in the harness.** The SM8550 NPU bundle answers the short probe and
  the fact-first probe correctly. On fact-last and fact-middle it answers as if the question at
  the end of the prompt were missing, e.g. "This question requires understanding of workplace
  etiquette…". That is the pattern of lost prefill chunks after the first. It still reports
  3320 tok/s prefill and 63 tok/s decode, which is why speed alone cannot be trusted.
- **LiteRT-LM GPU** prefills 5× faster than LiteRT CPU on the S23, but decodes slower (31.0 vs 37.4).
- **llama.cpp OpenCL on the S23** loads and runs on rc.7 (the 0.12.4-era hang did not recur), but
  it is far slower than CPU on prefill (64.6 vs 253.1).
- The S23 NPU file on the phone is named `Gemma3-1B-IT_sm8550.litertlm`. Its size (690143232
  bytes) matches `Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm` in the HF `litert-community/Gemma3-1B-IT`
  tree. SHA-256 `29426fd343eaeac9d54d67ca2bf72fce8c1f0cce947757e5e9c8009bcf5e72af`.

Evidence bundle (main matrix + bug reproduction, 6 sessions, verified):
`1890e4b41626dbad97995990e8e1eadb838baa1a278989be7a4bbb354b18f403`.
APK `8f14f0ffba3d4f01ba63fef637ebe829dbc56d49aef7f0fe783c9a4ce6494b25`.

## Legacy Hexagon row check (POCO F8 Ultra, Llama 3.2 1B, 2026-10-17)

The board's legacy llama.cpp NPU row for the F8 Ultra (Llama 3.2 1B, Q4_0, **197.6 prefill /
38.6 decode tok/s**, n=3, prompt `std-pp512-v1`, `validation: legacy`) was measured without any
output check. Because llama.cpp Hexagon produced garbage on Gemma 3 1B above, the same gate was run
on Llama 3.2 1B: `xebenchapp/configs/f8-llama32-hexagon-check-v1.json`. It has llama.cpp CPU as the
gate reference and llama.cpp Hexagon (`HTP0`, `nGpuLayers` 99), for both the pinned reference GGUF
(`Llama-3.2-1B-Instruct-Q4_K_M.gguf`, MODELS.md) and the Q4_0 file of the legacy row (not pinned;
SHA-256 below). It ran 4 repetitions each, with the same prompt text and probes as above, wrapped
in the Llama 3.2 user turn (`chatTemplate: llama32`, no system message). It used APK
`1cf22fb1845e567e84ab64cc74029b993d6d801acfc25ca47d38fb154b673919` (commit `e33ac64`) with
llama.rn 0.12.4.

| Engine | Backend | Artifact | Prefill tok/s | Decode tok/s | TTFT ms | n | Observed backend | Answer probes (capital, fact last, first, middle) | Comparison |
|---|---|---|---|---|---|---|---|---|---|
| llama.cpp | CPU | Q4_K_M | 204.8 (2.2) | 47.6 (1.2) | 2251 (26) | 4/4 | consistent | pass, pass, pass, pass | valid (reference) |
| llama.cpp | Hexagon | Q4_K_M | 213.6 (1.0) | 39.6 (0.1) | 2157 (10) | 4/4 | confirmed (`HTP0`) | pass, **fail**, **fail**, **fail** | **invalid** |
| llama.cpp | CPU | Q4_0 | 301.8 (2.8) | 53.1 (0.4) | 1528 (15) | 4/4 | consistent | pass, fail ("Peanut"), pass, pass | invalid (its own gate failed) |
| llama.cpp | Hexagon | Q4_0 | 1211.6 (14.9) | 51.8 (1.3) | 382 (5) | 4/4 | confirmed (`HTP0`) | pass, **fail**, **fail**, **fail** | **invalid** |

All four sessions passed run conditions with charging eligibility satisfied. Evidence bundle
`7cd4368bf08f339b78e731c2eed789768de44f736f338ad38f06ee154c3ae478` (4 sessions, verified).
Q4_0 SHA-256 `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`; Q4_K_M
`6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`.

Findings:

- **Hexagon output is wrong for Llama 3.2 1B, for both quantizations, on every prompt longer than
  about 128 tokens.** The short prompt (about 20 tokens) is answered correctly. The long probes and the
  measured ~460-token prompt degenerate into repeated fragments ("which, which, which …",
  "ususus…"). The CPU run of the same file answers them correctly (Q4_K_M) or misses one (Q4_0,
  which is a model-quality miss on CPU too). Together with the Gemma 3 result above, where even the
  short probe failed, the llama.rn 0.12.4 Hexagon backend does not produce trustworthy output for
  these models on this phone.
- **The legacy row cannot be trusted as a measurement of correct inference.** It used a ~512-token
  prompt, which is the failing regime here, and it had no output check. Its speed is not directly
  comparable to these rows:
  - The July build's llama.rn version is not recorded.
  - It used the `std-pp512-v1` prompt with a system message.
  - Today, the Q4_0 Hexagon prefill is 1211.6 tok/s against the legacy 197.6, while decode is
    51.8 against 38.6.
  
  The legacy figures are close to today's **Q4_K_M** Hexagon row (213.6 / 39.6), whose output is
  wrong. Whether the July build produced correct output cannot be established from the record.
  Recommendation for the board (a PM-gated step, not done here): mark that row as unverified for
  output correctness, or withdraw it, until a Hexagon build passes the gate.
- Hexagon on Q4_K_M is not faster than CPU (213.6 vs 204.8 prefill, 39.6 vs 47.6 decode),
  consistent with the old note that Q4_K_M largely falls back on the DSP.

## llama.rn 0.13.0-rc.7 re-check (POCO F8 Ultra, 2026-10-18)

The llama.cpp rows above, and the legacy check, were measured with llama.rn 0.12.4, the
version the harness pinned until now. PocketPal ships llama.rn 0.13.0-rc.7 (npm `latest`,
2026-10-05). The llama.cpp rows were therefore re-run with rc.7 on the same phone, models,
prompt and gate. LiteRT-LM rows are unaffected: LiteRT-LM does not use llama.rn.

**Build.** APK `8f14f0ffba3d4f01ba63fef637ebe829dbc56d49aef7f0fe783c9a4ce6494b25` (branch commit
`a66b65e`). llama.rn rc.7 sets `rnllamaBuildFromSource=true` in its own
`android/gradle.properties` ("force from-source build until we ship new prebuilts"). Any app on
rc.7 therefore compiles the core native library from the vendored llama.cpp sources; the npm
prebuilts predate those sources. Here that was NDK 27.1.12297006, RelWithDebInfo, with the
Hexagon SDK 6.4.0.2 present. The packaged `librnllama_v8_2_dotprod_i8mm_hexagon_opencl.so` has ELF
build ID `3b9a94783aa1b08315ecbe2b2d5a8e634421064c`. The DSP-side `libggml-htp-v81.so` is the npm
prebuilt. Later runs align the toolchain with PocketPal (NDK 27.3.13750724); these runs did not.

### Gemma 3 1B, llama.cpp rows (`f8-gemma3-llamacpp-rc7-v1`, 10 repetitions)

| Backend | 0.12.4 prefill / decode | 0.12.4 gate | rc.7 prefill / decode (IQR) | rc.7 gate | rc.7 comparison |
|---|---|---|---|---|---|
| CPU | 394.9 / 50.6 | pass | 381.2 (0.6) / 51.3 (0.1) | pass (reference) | valid |
| OpenCL | 342.9 / 27.9 | pass | 937.7 (3.2) / 38.1 (0.2) | pass | valid |
| Hexagon (`HTP0`) | 256.5 / 71.0 | **fail (garbage)** | 1249.7 (19.2) / 24.0 (5.7) | **pass** | valid |

All rc.7 sessions: 10/10, conditions pass, charging satisfied, backend confirmed for OpenCL and
Hexagon. Bundle `beb9a67ced8709bf195a64d048be0d8529505f5b76e24ee612de2d8867046bd2`.

### Llama 3.2 1B Hexagon check (`f8-llama32-hexagon-check-v1`, 4 repetitions)

| Run | 0.12.4 prefill / decode | 0.12.4 long probes | rc.7 prefill / decode (IQR) | rc.7 answer probes (capital, last, first, middle) | rc.7 comparison |
|---|---|---|---|---|---|
| CPU Q4_K_M | 204.8 / 47.6 | pass | 207.2 (0.2) / 47.3 (0.6), n=3/4 | pass, pass, pass, pass | valid (reference) |
| Hexagon Q4_K_M | 213.6 / 39.6 | **fail** | 4007.2 (43.5) / 57.7 (0.2) | pass, pass, pass, pass | **valid** |
| CPU Q4_0 | 301.8 / 53.1 | one miss | 304.3 (0.2) / 52.5 (0.3) | pass, fail ("Peanut"), pass, pass | invalid (own gate failed) |
| Hexagon Q4_0 | 1211.6 / 51.8 | **fail** | 4204.4 (53.8) / 64.2 (1.7) | pass, fail ("Peacock"), pass, pass | unverified (reference failed) |

The rc.7 Q4_K_M CPU reference's fourth repetition was skipped because the battery did not cool
back to within 2 °C of the baseline in 900 s. Its condition verdict is therefore `fail
(readiness-timeout)`; the three measured repetitions are kept. Bundle
`20009fec0a56fe4bcb2b0046a2e160e3fca5c9263de1fef8a364dc62674ca210`.

### What changed

- **llama.cpp Hexagon output is correct on rc.7** for Gemma 3 1B and Llama 3.2 1B Q4_K_M. Every probe
  agrees with the CPU reference, including the multi-chunk ones. Llama 3.2 Q4_0 misses one probe
  exactly where the Q4_0 CPU reference also misses it, so that is not a Hexagon fault. The
  garbage output in the sections above is a property of llama.rn 0.12.4, not of the Hexagon path
  in general.
- **rc.7 Hexagon is much faster at prefill** (Llama 3.2: 4007–4204 tok/s against about 200 on CPU).
  Decode is faster than CPU for Llama 3.2 (57.7–64.2 vs 47.3–52.5) but slower and noisier for
  Gemma 3 (24.0, IQR 5.7, against 51.3 on CPU).
- **rc.7 OpenCL** is much faster than 0.12.4 OpenCL on the F8 (937.7 vs 342.9 prefill).
- **The legacy board row** (Llama 3.2 1B Q4_0, 197.6 / 38.6) matches neither version's Hexagon
  numbers on today's prompt, and it was never output-checked. It remains unverified. rc.7 is the
  version to measure going forward.
- On this build llama.rn's default devices no longer include HTP (Hexagon is opt-in in rc.7), so
  CPU runs attach only `GPUOpenCL`. Backend evidence for llama.cpp CPU stays `consistent`.

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
