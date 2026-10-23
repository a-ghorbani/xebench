# Model refresh spike (POCO F8 Ultra, 2026-10-22)

Lab notes for the model refresh ([#43](https://github.com/a-ghorbani/xebench/issues/43)). Question: does every new
artifact × backend planned for the POCO F8 Ultra load in the app, pass the output gate, and what does the configurations
pull request need to know? **These are spike sessions, not board measurements:** one gated run plus one measured
repetition per setup, under the board's run conditions (120 s cooldown, 900 s readiness timeout, 2 °C rise bound,
std-pp512-v1, 128-token decode budget), so the speeds are indicative only. The sessions are in the history
(evidence below); no configuration here is a board configuration except the LiteRT-LM NPU check in section 1, which
re-ran the board's own file.

## Setup

| Item | Value |
| --- | --- |
| Phone | POCO F8 Ultra (`25102PCBEG`, SM8850), Android 16, 11,286,132 kB `MemTotal`; tethered, battery `FULL` throughout (charging satisfied in every session) |
| Experimental APK | `xebench-experimental-release-4c43d6e.apk`, SHA-256 `914bb5fd…1451` (branch `spike/model-refresh` at `4c43d6e`, clean release build): llama.rn 0.13.0-rc.8 (llama.cpp b11804, `c7fc509`), litertlm-android 0.18.1 + litert-npu-runtime-qualcomm 2.3.0 + QAIRT 2.50.0.260828221209, react-native-executorch 0.10.7 (ExecuTorch 1.4.1, XNNPACK) |
| GENIE-X APK | `xebench-geniex-release-4c43d6e.apk`, SHA-256 `fa44ddf3…7efe67`: geniex-android 0.8.0 with QAIRT 2.50.0.260828221209 |
| Package | Both flavors are `com.xebenchapp` (no `applicationIdSuffix`), so one is installed at a time: experimental 21:25 UTC, GENIE-X 22:35, experimental again 22:45 (left installed). Each install's `base.apk` hash was checked on the phone |
| Files | Every file downloaded at its pinned revision ([MODELS.md](../../MODELS.md)), SHA-256 checked on the host and again on the phone after the push (`stage_model.py` for the GGUF files, `stage_geniex_bundle.py --push` for the GENIE-X bundle, a `.partial` push and on-device `sha256sum` for the rest). Every session's artifact hash equals its pin |
| Configurations | `xebenchapp/configs/spike-refresh-*.json`, committed before the runs (`9da1e78`, `f7b2e4f`) |

## Results

One measured repetition each; prefill and decode in tok/s from each engine's own timings (GENIE-X NPU prefill is
`ttft-derived`). "Prompt" is the engine's own prompt token count for std-pp512-v1; "decode" is the tokens the
repetition decoded. PSS is the app's PSS at the repetition's `after` snapshot, max RSS the largest 1 Hz sample during
load and inference (MiB).

| Model | Engine / backend | Loads | Gate | Prompt | Decode | Prefill | Decode tok/s | Backend evidence | PSS / max RSS | Issues |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gemma 3 1B (SM8850 bundle) | LiteRT-LM 0.18.1 NPU (n=3) | yes | pass | 519 | 128 (fixed) | 4725.5 | 57.6 | confirmed, litert-npu-log-v1 | 714 / 812 | none |
| Gemma 3 1B int4 | LiteRT-LM 0.18.1 CPU (n=3, gate reference) | yes | pass | 519 | 128 (fixed) | 662.4 | 42.0 | consistent | 1194 / 1294 | decode 13 % below the 0.18.0 session (48.1) |
| Gemma 4 E2B Q4_0 (QAT) | llama.cpp rc.8 CPU | yes | pass | 518 | **85** (end of turn) | 128.3 | 26.7 | consistent (`n_gpu_layers` 0) | 3910 / 3745 | stops at `<turn\|>` |
| Gemma 4 E2B Q4_0 (QAT) | llama.cpp rc.8 OpenCL | yes | pass | 518 | **79** | 410.0 | 29.7 | confirmed (`GPUOpenCL`) | 2510 / 2385 | stops at `<turn\|>`; GPU buffers outside RSS |
| Gemma 4 E2B Q4_0 (QAT) | llama.cpp rc.8 Hexagon | yes | pass | 518 | **83** | 1601.0 | 30.7 | confirmed (`HTP0`) | 2384 / 2460 | stops at `<turn\|>` |
| Qwen3 1.7B Q4_0 | llama.cpp rc.8 CPU | yes | pass | 519 | 127 (budget) | 203.1 | 36.0 | consistent | 1755 / 1841 | none |
| Qwen3 1.7B Q4_0 | llama.cpp rc.8 OpenCL | yes | pass | 519 | 127 | 654.1 | 34.5 | confirmed (`GPUOpenCL`) | 517 / 808 | none |
| Qwen3 1.7B Q4_0 | llama.cpp rc.8 Hexagon | yes | pass | 519 | 127 | 2546.5 | 44.6 | confirmed (`HTP0`) | 438 / 537 | none |
| Gemma 3 1B Q4_0 (QAT) | llama.cpp rc.8 CPU | yes | pass | 518 | 127 | 396.3 | 50.0 | consistent | 1273 / 1351 | none |
| Gemma 3 1B Q4_0 (QAT) | llama.cpp rc.8 OpenCL | yes | pass | 518 | **16** (end of turn) | 961.3 | 41.3 | confirmed (`GPUOpenCL`) | 1399 / 1476 | decode over 16 tokens only |
| Gemma 3 1B Q4_0 (QAT) | llama.cpp rc.8 Hexagon | yes | pass | 518 | 127 | 3600.0 | 46.7 | confirmed (`HTP0`) | 820 / 897 | none |
| Gemma 4 E2B (mixed 2/4/8-bit) | LiteRT-LM 0.18.1 CPU | yes | pass | 519 | 128 (fixed) | 255.6 | 29.2 | consistent | 1615 / 1702 | none; render check passed |
| Qwen3 1.7B int4 | LiteRT-LM 0.18.1 CPU | yes | **fail** | 519 | 128 (fixed) | 91.7 | 10.6 | consistent | 2360 / 2446 | every gate answer is empty text (thinking); slow |
| Gemma 4 E2B 8da4w (mixed 4/16-bit) | ExecuTorch 0.10.7 XNNPACK | yes | pass | 519 | 128 (fixed) | 66.7 | 6.8 | confirmed (program delegates) | 2298 / 2364 | slow; 2 prefill forwards of 512 |
| Qwen3 1.7B 8da4w | ExecuTorch 0.10.7 XNNPACK | yes | pass | 519 | 128 (fixed) | 321.6 | 31.2 | confirmed (program delegates) | 2097 / 2345 | none |
| Qwen3 1.7B W4A16 ("for Galaxy" SM8850 bundle) | GENIE-X 0.8.0 NPU | yes (after a staging fix) | pass | **520** | 128 (budget) | 2388.0 | 28.8 | log events all present; rule pending | 239 / 1272 | first attempt: bundle directory unreadable |
| Qwen3 1.7B Q4_0 | GENIE-X 0.8.0 CPU (llama_cpp, gate reference) | yes | pass | 519 | 128 (budget) | 155.8 | 34.2 | consistent | 1927 / 1995 | none |

The LiteRT-LM Gemma 4 and both ExecuTorch rows are from the `-v2` configurations; the `-v1` runs of the same files
gave the same results within 2.5 % (LiteRT-LM Gemma 4: 261.8 / 29.5; ExecuTorch Gemma 4: 66.0 / 6.7; ExecuTorch Qwen3:
321.0 / 31.5) but cannot be exported (see "For the configurations pull request", item 6).

## 1. LiteRT-LM 0.18.1 NPU check (Gemma 3 1B)

The board configuration [`litertlm-gemma3-sm8850-npu-v2`](../../xebenchapp/configs/litertlm-gemma3-sm8850-npu-v2.json),
unchanged, on the experimental APK with litertlm-android 0.18.1, litert-npu-runtime-qualcomm 2.3.0 and QAIRT
2.50.0.260828221209 (3 repetitions, the board's own protocol):

| Run | 0.18.0 board session (prefill / decode) | 0.18.1 (prefill / decode, median of 3) | Gate | Decode | Backend |
| --- | --- | --- | --- | --- | --- |
| NPU, SM8850 bundle | 4578.9 / 56.9 | 4725.5 / 57.6 | pass (4 of 4 answers exact matches of the CPU reference) | 128 fixed, every repetition | confirmed by litert-npu-log-v1 in every repetition |
| CPU int4 (gate reference) | 693.8 / 48.1 | 662.4 / 42.0 | pass | 128 fixed | consistent |

- The log shows every litert-npu-log-v1 event: `Loading shared library: …libLiteRtDispatch_Qualcomm.so`,
  `BackendType : Htp(2)`, the FastRPC `libQnnHtpV81Skel.so … _dom=cdsp` open, and two `Creating new QNN context for
  bytecode` lines inside each of the four NPU loads (gate and three repetitions). Publication policy v1 reports the NPU
  session `verified` with backend `confirmed for npu by the app log (litert-npu-log-v1)`, and the CPU session `verified`.
- `runtimeVersions`: `litertLm` 0.18.1, `litertNpuRuntimeQualcomm` 2.3.0, `qairt` 2.50.0.260828221209.
- **Verdict: LiteRT-LM 0.18.1 passes the F8 NPU check**: same output, log-confirmed backend, fixed 128-token decode,
  NPU speeds within 3.2 % (prefill) and 1.2 % (decode) of the 0.18.0 session.
- The CPU reference decoded 13 % slower than in the 0.18.0 session (42.0 against 48.1 tok/s; prefill 4.5 % slower),
  consistently over its three repetitions (41.8–42.0). One session cannot separate 0.18.1 from the phone's state; the
  LiteRT-LM CPU board sessions will show whether it holds.
- The capture's first gate stalled for about 15 minutes: the phone's swipe keyguard (no PIN) had come back after the
  screen timed out, and the app started behind it. `input keyevent KEYCODE_WAKEUP` plus `wm dismiss-keyguard` (the
  precedent in [reference-config-v2](reference-config-v2.md)) let it continue; the stall was before any measurement.
  Every later capture dismissed the keyguard first.

## 2. llama.cpp (llama.rn 0.13.0-rc.8)

Each model ran in its own configuration: CPU (the gate reference), OpenCL (`nGpuLayers` 99) and Hexagon (`nGpuLayers`
99, `devices: ["HTP0"]`), with cpu-reference-v3's load settings (no mmap, no mlock, repacking on, flash attention auto),
4 threads, `n_ctx` 2048, std-pp512-v1.

- **Every backend offloads.** The OpenCL runs report `gpu: true` with device `GPUOpenCL`, the Hexagon runs `HTP0`, and
  `effectiveSettings.init` carries `n_gpu_layers` 99 (and `devices: ["HTP0"]`); backend `confirmed`. The CPU runs carry
  `n_gpu_layers` 0 and no devices and report `GPUOpenCL` attached, which is `consistent` as before. Native libraries:
  `librnllama_v8_2_dotprod_i8mm_hexagon_opencl.so` (ELF build ID `06ece4b0…`).
- **Every gate passes**, including Gemma 4 E2B on OpenCL and Hexagon; the Gemma 4 Hexagon and OpenCL answers are exact
  matches of the CPU reference.
- Gemma 4 E2B on Hexagon prefills 12.5× faster than on the CPU (1601 against 128 tok/s); Qwen3 12.5× (2547 against
  203); Gemma 3 9.1× (3600 against 396). Hexagon decode is faster than CPU for Gemma 4 and Qwen3, slightly slower for
  Gemma 3.
- **Memory with Gemma 4 E2B** (3.35 GB file): the CPU run's PSS reached 3910 MiB (max RSS 3745, process high-water mark
  3751). The OpenCL and Hexagon runs kept less in the app's RSS (2385 and 2460 MiB) but more outside it: the phone's
  `MemAvailable` fell from about 7.0 GB at idle to 2.63 GB during the Hexagon repetition and to 2.9 GB during the
  OpenCL load while the app's RSS was 0.4–0.6 GB (device buffers are not in RSS). Nothing was killed on this 12 GB phone.

### Decode length (llama.cpp stops at the end of turn)

The harness has no fixed decode for llama.cpp (`decodeMode: fixed` is accepted for LiteRT-LM and ExecuTorch only), so a
repetition stops at the first end-of-generation token or after 128 tokens (`decodeTokens` 127: llama.cpp counts the
tokens after the first, which the prompt evaluation samples). On std-pp512-v1:

- Qwen3 1.7B and Gemma 3 1B (CPU, Hexagon) reach the budget (127).
- **Gemma 4 E2B stops at `<turn|>` on every backend, after a different number of tokens: 85 (CPU), 79 (OpenCL), 83
  (Hexagon).** Its decode speeds are therefore over different lengths per backend.
- **Gemma 3 1B on OpenCL stopped after 16 tokens** while its CPU and Hexagon runs reached 127; the gate (which uses the
  Gemma 3 chat turn) passed, so the measured std-pp512-v1 continuation on OpenCL diverged from the CPU's and hit
  `<end_of_turn>` early. A decode rate over 16 tokens is not comparable.
- llama.rn 0.13.0-rc.8 has `ignore_eos`, but it is not enough for a fixed decode: `cpp/jsi/JSIParams.cpp` sets the
  logit of `llama_vocab_eos` alone to −∞, and the completion loop (`cpp/rn-completion.cpp`) still stops on any
  `llama_vocab_is_eog` token. The Gemma 4 GGUF's EOS is `<eos>` (1) while the end of turn is `<turn|>` (106), so
  `ignore_eos` would not stop Gemma 4 (or Gemma 3, whose end of turn is `<end_of_turn>`) from ending at its turn end.
  Qwen3's GGUF EOS is `<|im_end|>` (151645), but `<|endoftext|>` (151643) is also an end-of-generation token. A
  fixed llama.cpp decode needs a logit bias of −∞ on every end-of-generation token of the vocabulary (`logit_bias`,
  which llama.rn passes through), not `ignore_eos`. Read from the rc.8 package sources; not run on the phone.

## 3. LiteRT-LM 0.18.1 CPU (Gemma 4 E2B, Qwen3 1.7B)

- **Gemma 4 E2B** (`gemma-4-E2B-it.litertlm`, mixed 2/4/8-bit): loads in 0.2 s, the pass-through render check passes
  (the measured repetition prefilled std-pp512-v1 verbatim, 519 tokens: LiteRT-LM adds the artifact's start token as
  for Gemma 3), the gate passes through the artifact's own template, and the fixed decode gives exactly 128 tokens.
  255.6 / 29.2 tok/s. PSS 1615 MiB, max RSS 1702 MiB, of which about 1.26 GB is file-backed (the mapped model);
  `MemAvailable` stayed above 6.6 GB.
- **Qwen3 1.7B** (`Qwen3-1.7B_dynamic_wi4b32_afp32.litertlm`, int4): loads, the render check passes (519 tokens, no
  start token, equal to llama.cpp's count) and the fixed decode gives 128 tokens, but **the gate fails: every probe's
  reply text is empty**, including the measured-prompt probe at 128 tokens. The gate took about 40 s, so tokens were
  generated. The gate sends each probe through the artifact's own chat template, which thinks by default; the reply
  most likely stays inside the think block (or LiteRT-LM puts it outside `Content.Text`), so the visible text is
  empty. The record cannot tell the two apart. It is also slow: 91.7 / 10.6 tok/s, with PSS 2360 MiB (the file keeps
  32-bit activations).

## 4. ExecuTorch 0.10.7 (XNNPACK)

- **Both `.pte` files load** with their pinned tokenizers (`tokenizerSha256` checked by the app).
- **Program metadata of the 3.2 GB Gemma 4 E2B `.pte` reads:** `forward` delegates to `XnnpackBackend`, the other
  methods (`enable_dynamic_shape`, `get_bos_id`, `get_eos_ids`, `get_max_context_len`, `get_max_seq_len`,
  `get_model_schema`, `get_vocab_size`, `use_kv_cache`) delegate nothing, `XnnpackBackend` is registered, and the only
  ExecuTorch libraries mapped are `libexecutorch.so` and `libxnnpack_executorch_backend.so`: backend `confirmed`
  (`program-delegates`). The same for Qwen3.
- **`<bos>` prefix:** Gemma 4 E2B prefills 519 tokens (`numPromptTokens`), which is the HF tokenizer's 518 text tokens
  plus one BOS, the count LiteRT-LM reports and the count `llama-tokenize` gives for the GGUF (519, BOS first). llama.rn
  reports **518** for the same text and GGUF, for Gemma 3 as before; for Qwen3 (no BOS) all three engines report 519.
  So the prefix does what it should (one BOS, no double BOS), and the off-by-one is llama.rn's prompt count for BOS
  models, not ExecuTorch's.
- Gates pass on both; the replies carry the end-of-turn token as text (`Paris<turn|>`, `Paris.<|im_end|>`).
- Fixed decode: 128 tokens (`emittedTokens` 128, `numGeneratedTokens` 127 in the decode loop).
- Chunked prefill: Gemma 4 E2B `prefillChunkTokens` 512, `prefillChunks` 2; Qwen3 2047 and 1.
- **Gemma 4 E2B is slow on this path: 66.7 / 6.8 tok/s** (Qwen3: 321.6 / 31.2). Load 4.0 s. PSS 2298 MiB, max RSS
  2364 MiB (high-water mark 2492), `MemAvailable` above 5.1 GB.
- The ExecuTorch runs created empty `DCIM`, `Download`, `Movies`, `Music`, `Pictures` and `Ringtones` directories in the
  app's external files directory (22:29 UTC, the first ExecuTorch launch).

## 5. GENIE-X 0.8.0 (Qwen3 1.7B NPU)

- **The "for Galaxy" SM8850 bundle loads and runs on the non-Galaxy F8** (QAIRT 2.50.0.260828221209 from the APK,
  `HTP arch v81 detected.`). Gate pass against GENIE-X's own llama_cpp CPU run (3 of 4 answers exact matches; the fourth
  is `417` against `The review is held in room 417.`). 2388.0 / 28.8 tok/s, load 4.7 s, PSS 239 MiB after the
  repetition (max RSS 1272 during load).
- **Decode length:** on std-pp512-v1 Qwen3 decoded the full 128 tokens on the NPU and on the llama_cpp CPU plugin; it
  did not reach the end of turn.
- **Prompt tokens: 520 on the NPU, 519 on the CPU.** The QAIRT pipeline prepends a BOS for Qwen3: `first-turn BOS=true`,
  `ids start with [151643, …]` (`<|endoftext|>`). [geniex-inapp.md](geniex-inapp.md) states that it prepends a BOS for
  Gemma 4 only, so a Qwen3 prompt gets none; the device shows otherwise.
- **First attempt refused:** the NPU run was recorded `setup-failed` ("the model artifact could not be read").
  `stage_geniex_bundle.py --push` leaves the bundle directory owned by `shell` with mode `2770`
  (`drwxrws---`, group `ext_data_rw`); the app's uid is not in that group, so it cannot list or open the directory
  (single files pushed the same way are `-rw-rw-rw-` and readable). After `chmod 2775` on that directory the rerun
  loaded. The CPU session of the first attempt passed and is in the bundle too.
- **geniex-npu-log-v1 against the real log.** The capture kept these lines (pid-scoped, install path shortened here):

  ```
  I GenieXSdk: HTP arch v81 detected.
  I GenieXSdk: HTP runtime path: /data/app/…/lib/arm64 (auto-resolved from geniex_core directory (flat layout))
  I com.xebenchapp: vendor/qcom/proprietary/adsprpc/src/fastrpc_apps_user.c:3419: remote_handle64_open: opened handle 0x… (remote 0x…) for file:///libQnnHtpV81Skel.so?qnn_2_50_0_skel_handle_invoke&_modver=1.0&_dom=cdsp on domain 3 (spawn time …
  I GenieXSdk: HTP graphs will execute on 1 core (default; set num_cores or add htp_backend_ext_config.json `cores` entries to request more)
  I GenieXSdk: prompt: 520 tokens, first-turn BOS=true, ids start with [151643, 27, 91, 7265, 3575]
  ```

  The gate's load logged the same four lines plus one `prompt:` line per probe (25, 267, 267, 498 and 465 tokens).
  Every pattern matches as written; these release-AAR lines carry no `[file:line:func]` prefix (the llama_cpp plugin's
  lines do, at V/D level). `htp-arch` is logged before `htp-runtime`. The policy's evaluation of the bundle: the
  repetition is confirmed with no missing event, and the session's backend stays unknown because the rule is in
  `PENDING_DEVICE_CONFIRMATION`. Removing it from that set is a reviewed change, not part of this spike.
- geniex-android logs every request's full prompt and reply text at V level under `GenieXSdk`
  (`LlmGenerateInput(prompt_utf8: …)`, `LlmGenerateOutput(full_text: …)`). The capture keeps only rule lines, so none of
  it reaches the evidence; a raw logcat of the app does contain it.
- The llama_cpp plugin pins its 4 threads to cores 2–5 (`threadpool attached: … pinned_cores=[2,3,4,5]`).

## Gemma 4 E2B memory, by engine

| Engine / backend | File | App PSS after the repetition (MiB) | App max RSS (MiB) | Lowest `MemAvailable` (sampled every 5 s) |
| --- | --- | --- | --- | --- |
| llama.cpp CPU | 3.35 GB GGUF, read into memory | 3910 | 3745 | 3.2 GB (gate load) |
| llama.cpp OpenCL | same | 2510 | 2385 | 2.9 GB (load; app RSS 0.4–0.6 GB) |
| llama.cpp Hexagon | same | 2384 | 2460 | 2.63 GB |
| LiteRT-LM CPU | 2.59 GB `.litertlm`, mapped | 1615 | 1702 (1.26 GB file-backed) | 6.6 GB |
| ExecuTorch XNNPACK | 3.2 GB `.pte`, mapped | 2298 | 2364 | 5.1 GB |

Idle `MemAvailable` was about 7.0–7.4 GB with the app idle. The host sampler read `/proc/<pid>/status` of the app and
`/proc/meminfo` every 5 s, so it misses short peaks; the app's own 1 Hz `maxRssMb` is the max RSS column. On the
Galaxy S23 (8 GB) the llama.cpp CPU figure is the one to watch: about 3.9 GB of PSS for the app alone.

## For the configurations pull request

1. **LiteRT-LM 0.18.1 can be pinned**: the F8 NPU check passes (section 1).
2. **llama.cpp needs a fixed decode or a decode-length rule.** Gemma 4 E2B ends its turn after 79–85 tokens on
   std-pp512-v1 and the count differs by backend; Gemma 3 1B on OpenCL ended after 16. `ignore_eos` in llama.rn rc.8
   masks only EOS; a fixed decode needs `logit_bias` −∞ on every end-of-generation token. Otherwise the rows must state
   "decode N of 128 tok" and the OpenCL Gemma 3 row is not comparable.
3. **llama.rn's prompt count is one lower for BOS models** (Gemma 3 and Gemma 4: 518, where the GGUF tokenizer, LiteRT-LM
   and ExecuTorch give 519). The docs' "llama.cpp counts 518" is llama.rn's timing count, not the tokens prefilled.
4. **LiteRT-LM Qwen3 int4 fails the gate** with empty replies (thinking through the artifact's template). The gate needs
   thinking off for this file (for example the harness's `qwen3` turn sent through the pass-through template, as the
   measured repetitions do), or the configuration cannot pass. Its speed (91.7 / 10.6) is far below every other Qwen3
   path.
5. **GENIE-X**: (a) `stage_geniex_bundle.py --push` must leave the bundle directory readable by the app (mode `2775`,
   or created by the app); (b) the QAIRT pipeline prepends BOS 151643 for Qwen3, so the NPU prompt is 520 tokens against
   519 everywhere else; geniex-inapp.md needs the correction; (c) geniex-npu-log-v1 matched every real line (above), so the
   rule can leave `PENDING_DEVICE_CONFIRMATION` in a reviewed change; (d) Qwen3 decodes the full 128 tokens on
   std-pp512-v1 here, so no row note is needed for this prompt.
6. **The board's quant labels cannot be exported.** `export_evidence.py`'s label alphabet
   (`[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,127}`) has no `/` or `,`, so `mixed 2/4/8-bit`, `mixed 4/16-bit` and `4-bit weights,
   8-bit activations` (docs/STYLE.md, `check_public_text.mjs`) are refused: `Export refused: …/quant: Approved field is
   not a label`. The `-v2` spike configurations use `mixed 2-4-8-bit`, `mixed 4-16-bit` and `4-bit weights 8-bit
   activations` to get bundles. The export alphabet (and its tests) must accept the board labels before the board
   configurations use them.
7. **ExecuTorch Gemma 4 E2B is slow** (66.7 / 6.8 tok/s against 321.6 / 31.2 for Qwen3 on the same runtime). Worth a
   look before its board sessions (thread count, the fp16 attention and per-layer-embedding layers).
8. The `spike-refresh-*` configurations are committed reviewed files, so publication policy v1 rates their sessions
   `verified`; only `experiment-*` configuration ids are excluded from publication. They stay off the board because
   `configs/board.json` does not list them. The Gemma 3 OpenCL session with its 16-token decode is also `verified`: the
   policy does not look at decode length.
9. Lab procedure: dismiss the swipe keyguard before each capture (`KEYCODE_WAKEUP`, `wm dismiss-keyguard`); with it
   showing, the app starts behind it and stalls.

## Galaxy S23: Gemma 4 E2B memory probe (2026-10-23)

Question: can Gemma 4 E2B run on the Galaxy S23 (8 GB), per engine, and with how much headroom? Same experimental APK
(`914bb5fd…1451`, installed 02:22 UTC, hash checked on the phone), same files (staged and hashed on the phone as on the
F8), one gated run plus one measured repetition, board run conditions. Configurations: the device-independent
`spike-refresh-litertlm-gemma4-cpu-v2`, `-executorch-gemma4-v2`, `-litertlm-qwen3-cpu-v1` and `-executorch-qwen3-v2`,
plus three CPU-only llama.cpp files committed before the runs (`b5124ea`): `spike-refresh-llamacpp-gemma4-cpu-v1`
(cpu-reference-v3's load settings, no mmap), `spike-refresh-llamacpp-gemma4-cpu-mmap-v1` (the same with `useMmap: true`,
an extra data point, not a board setting) and `spike-refresh-llamacpp-qwen3-cpu-v1`.

The phone: `MemTotal` 7,242,456 kB, 8 GB of zram swap. With the app idle, `MemAvailable` was 3.5 GB at the start and
3.96–4.98 GB before the later runs, and 1.0–1.9 GB of swap was in use: about 3.3–3.7 GB was held by the system and
other resident processes (taken as a total only). A host sampler read the app's `/proc/<pid>/status` and
`/proc/meminfo` every 5 s; kill evidence comes from `logcat -b events` and lmkd lines naming `com.xebenchapp`, and from
`dumpsys activity exit-info com.xebenchapp`. Thermal status was 0 throughout every run (headroom at most 0.67, battery
at most 32.3 °C).

| Model / engine | Result | Gate | Prompt / decode tokens | Prefill / decode tok/s | PSS after / max RSS (MiB) | Lowest `MemAvailable` (idle before the run) | Kill evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Gemma 4 E2B, LiteRT-LM 0.18.1 CPU | completes | pass | 519 / 128 fixed | 185.8 / 22.6 | 1464 / 1536 (1162 MiB file-backed) | 3576 MiB (idle 3869) | none |
| Gemma 4 E2B, ExecuTorch XNNPACK | completes | pass | 519 / 128 fixed | 55.0 / 6.7 | 2183 / 2227 | 2715 MiB (idle 4137) | none |
| Gemma 4 E2B, llama.cpp CPU, no mmap (board settings) | **killed by lmkd during the gate's model load** | — | — | — | — (high-water mark 3927 MiB; 2.7 GB of the app swapped out) | 816 MiB (idle 4602) | lmkd `Reclaim 'com.xebenchapp' … oom_score_adj 0 … to free 1094132kB rss, 2705232kB swap; reason: min2x watermark is breached even after kill; level: 2`; exit-info `reason=3 (LOW_MEMORY)` |
| Gemma 4 E2B, llama.cpp CPU, **mmap on** (extra) | completes | pass | 518 / **85** (end of turn) | 85.0 / 14.3 | 4476 / 4096 (2569 MiB file-backed) | 2661 MiB (idle 4243) | none |
| Qwen3 1.7B, llama.cpp CPU | completes | pass | 519 / 127 | 135.2 / 19.8 | 2085 / 1719 | 2727 MiB | none |
| Qwen3 1.7B, LiteRT-LM CPU | completes | **fail** (empty replies, as on the F8) | 519 / 128 fixed | 49.9 / 8.6 | 2259 / 2347 | 3374 MiB | none |
| Qwen3 1.7B, ExecuTorch XNNPACK | completes | pass | 519 / 128 fixed | 160.8 / 23.3 | 2252 / 2352 | 2481 MiB | none |

- **The killed capture has no record.** The app died about 26 s after its launch, while llama.rn read the 3.35 GB GGUF into
  anonymous memory for the CPU reference gate (sampled RSS 2.49 GB at 02:37:12, then the app was swapped down while
  `MemAvailable` fell to 816–892 MiB; killed at 02:37:28 UTC with `oom_score_adj` 0, the foreground app). The capture
  waited for the app until it was interrupted; it holds no session and is not in the bundle.
- **With `useMmap: true` the same file completes.** The weights stay file-backed and reclaimable (2569 MiB of the 4096 MiB max
  RSS), so lmkd had pages to drop. Its PSS (4476 MiB) counts those shared file pages. Decode stops at the end of turn
  after 85 tokens, as on the F8 CPU.
- ExecuTorch Gemma 4 E2B decodes at 6.7 tok/s on the S23 and 6.8 on the F8, although every other path is much faster on
  the F8.

Per-engine verdict for Gemma 4 E2B on the Galaxy S23:

| Engine | Verdict | Headroom |
| --- | --- | --- |
| LiteRT-LM CPU | viable | lowest `MemAvailable` 3576 MiB; app PSS 1464 MiB |
| ExecuTorch XNNPACK | viable (slow: 55.0 / 6.7 tok/s) | lowest `MemAvailable` 2715 MiB; app PSS 2183 MiB |
| llama.cpp CPU with the board's load settings (no mmap) | not viable: killed by lmkd during load | none |
| llama.cpp CPU with mmap | viable only with `useMmap: true` | lowest `MemAvailable` 2661 MiB; app max RSS 4096 MiB, 2569 MiB of it reclaimable file pages |

One run each; the kill depends on what else is resident (idle `MemAvailable` varied from 3.5 to 5.0 GB during the
session), so a no-mmap run could also survive on a better day, and an mmap run could still be killed with less free
memory.

For the configurations pull request (S23):

1. An S23 llama.cpp Gemma 4 E2B row cannot use cpu-reference-v3's `useMmap: false`. Either a separate configuration with
   `useMmap: true` (and its own row note: weights memory-mapped), or the cell becomes X on the S23.
2. LiteRT-LM and ExecuTorch Gemma 4 E2B fit with at least 2.7 GiB of `MemAvailable` left; no change needed.
3. Qwen3 1.7B loads on all three engines; the LiteRT-LM file fails the gate here too (item 4 above).

## Evidence

| Bundle | Sessions | What |
| --- | --- | --- |
| [`31f08310a8de…`](../../evidence/31f08310a8de323504572c42bc4b924ffe81f6b79c8a52a5707dc4acb0825f37/) | 6 | Galaxy S23 memory probe: LiteRT-LM and ExecuTorch Gemma 4 E2B, llama.cpp Gemma 4 E2B with mmap, Qwen3 1.7B on three engines |
| [`49a216f37945…`](../../evidence/49a216f37945edcc80b5d31a981206c1984e3293f16e4e47abd78adad2fe69e8/) | 2 | Section 1: `litertlm-gemma3-sm8850-npu-v2` on LiteRT-LM 0.18.1 (NPU and its CPU reference) |
| [`c12fb048be9b…`](../../evidence/c12fb048be9bfbf75361b33b57c3fd158510a84c45c369d4c35b07410c7b3880/) | 13 | Sections 2–4: the three llama.cpp configurations, LiteRT-LM Qwen3 (`-v1`), LiteRT-LM Gemma 4 and both ExecuTorch (`-v2`) |
| [`8c41c4e258ff…`](../../evidence/8c41c4e258ff4dd1fc149f165b8c45c7fe2cc3bb91f1db13b2db2acf078befb5/) | 4 | Section 5: both GENIE-X captures (the refused first attempt and the rerun) |

The three `-v1` captures with unexportable labels (LiteRT-LM Gemma 4, ExecuTorch Gemma 4 and Qwen3) stay local.
