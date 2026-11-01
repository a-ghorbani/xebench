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
| Configurations | `xebenchapp/configs/spike-refresh-*.json`, committed before the runs (`9da1e78`, `f7b2e4f` on the spike branch; on main as `74bf769`, `6855dbb`) |

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
| Gemma 4 E2B 8da4w (mixed 4/16-bit) | ExecuTorch (react-native-executorch 0.10.7) XNNPACK | yes | pass | 519 | 128 (fixed) | 66.7 | 6.8 | confirmed (program delegates) | 2298 / 2364 | slow; 2 prefill forwards of 512 |
| Qwen3 1.7B 8da4w | ExecuTorch (react-native-executorch 0.10.7) XNNPACK | yes | pass | 519 | 128 (fixed) | 321.6 | 31.2 | confirmed (program delegates) | 2097 / 2345 | none |
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

## 4. ExecuTorch 1.4.1 through react-native-executorch 0.10.7 (XNNPACK)

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
plus three CPU-only llama.cpp files committed before the runs (`b5124ea` on the spike branch; on main as `f7ee8a6`):
`spike-refresh-llamacpp-gemma4-cpu-v1` (cpu-reference-v3's load settings, no mmap),
`spike-refresh-llamacpp-gemma4-cpu-mmap-v1` (the same with `useMmap: true`, an extra data point, not a board setting) and
`spike-refresh-llamacpp-qwen3-cpu-v1`.

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

## ExecuTorch Gemma 4 E2B: why it decodes slowly (host analysis, 2026-10-23)

ExecuTorch 1.4.1 (react-native-executorch 0.10.7) decodes Gemma 4 E2B at 6.8 tok/s on the POCO F8 Ultra and 6.7 on the Galaxy S23, against 31.2 for
Qwen3 1.7B on the same runtime (sections 4 and Galaxy S23). This analysis was done on the host from the
react-native-executorch 0.10.7 package, the ExecuTorch v1.4.1 sources and the two pinned `.pte` files, which were
parsed as flatbuffers. It finds no harness setting behind the gap; the gap is in the export.

The board sessions of 2026-11-01 show the same gap on all three phones (decode tok/s, Gemma 4 E2B against Qwen3
1.7B): POCO F8 Ultra 6.9 against 31.3, Galaxy S23 6.7 against 23.3, Pixel 9 4.9 against 18.3. Finding
`executorch-gemma4-export` in [`data/findings.json`](../../data/findings.json) links here.

- **Threads.** react-native-executorch 0.10.7 does not resize ExecuTorch's threadpool and exposes no setting for it.
  Its only `_unsafe_reset_threadpool` call is in its `legacy/` code, which the harness does not import. ExecuTorch
  1.4.1 sizes the pool from `get_num_performant_cores()` (`extension/threadpool/threadpool.cpp`). Its cpuinfo
  heuristic discounts only efficiency core types (Cortex-A53, A55, A510, A520 and Apple's), so every core of the
  SM8850 counts: 8 threads. Both models run on the same pool, so threads do not explain the gap.
- **Runner.** react-native-executorch creates the runner itself (`create_text_llm_runner`, method `forward`,
  `LoadMode::Mmap`). It accepts `ignoreEos`, `maxNewTokens` and `temperature` from the caller, which the harness sets.
  Both exports use a KV cache with dynamic shapes. The Gemma export takes at most 512 tokens per forward and holds a
  4096-token context; the Qwen3 export takes 2048 per forward.
- **What `forward` runs.**

  | | Gemma 4 E2B | Qwen3 1.7B |
  | --- | --- | --- |
  | XNNPACK delegate calls | 953 (462 of them a single node) | 340 |
  | Calls to portable (non-delegated) kernels | 2,083 | 709 |
  | fp16↔fp32 casts (`_to_dim_order_copy`) | 881 | 0 |
  | KV-cache writes | 121 `index_put` and 92 `copy_` | 56 `update_cache` |
  | Linear-layer weights | 639 MB fp16 (205 layers) and 980 MB int4 | 860 MB int4 |

  In the Gemma export, RMSNorm's mean and the attention (SDPA) run in fp32 outside the fp16 delegates, so each layer
  casts its activations both ways. Its KV cache is int8 at a fixed full-window shape: 12 sliding layers of 1024 slots
  and 3 global layers of 4096, for K and V. Every forward, each decode step included, dequantizes the whole cache
  (about 18.9 million values) with single-threaded scalar kernels. It casts the result and copies the caches out of
  place. The Qwen3 export updates its cache in place.
- **Estimate.** Qwen3 moves about 0.9 GB of weights per decoded token at 31.2 tok/s. At that rate, Gemma's
  roughly 1.65 GB per token (fp16 attention and per-layer-embedding projections plus the int4 layers) would allow
  about 17 tok/s, or about 60 ms per token. The measured 147 ms per token leaves about 85 ms for the cache path and
  about 3,000 kernel dispatches per token. So the 16-bit weights account for about 1.9× of the gap and the
  partitioning of the export for the rest. Prefill is slow for the same reasons: each 512-token forward pushes about
  1.7 GB through the casts and runs its fp16 layers as fp16 matrix products instead of int8 kernels.
- **Software Mansion's own numbers.** Software Mansion's benchmark data (`docs/docs/04-benchmarks/imported.json` in
  the react-native-executorch repository) uses a Galaxy S26 Ultra (also SM8850), react-native-executorch 0.11.0-dev,
  64 greedy tokens and EOS ignored. It gives 4.5 tok/s for the Gemma 4 E2B 8da4w export and 15.9 for Qwen3 1.7B: the
  same pattern, at lower speeds than these sessions. Their Gemma figure is for the export at the repository's
  `v0.10.0` tag (2,635,017,344 bytes), which react-native-executorch 0.10.7's model registry downloads. MODELS.md
  pins a later revision, `27ecbb4` (3,215,277,312 bytes), whose change keeps the per-layer-embedding dequantization
  and sensitive projections at higher precision. The repository's model card gives no speed.

## Harness follow-ups

Changes made after the spike, in the harness (`harness/validation-followups`):

- **Board quant labels export** (item 6). `export_evidence.py`'s label form accepts `/` between two
  digits and `,` after a letter or digit and before a space and a letter or digit, so `mixed 2/4/8-bit`, `mixed 4/16-bit` and `4-bit weights, 8-bit
  activations` export as written; no label can hold a path segment, and the privacy screen runs
  unchanged. New configurations use the board labels directly; the `-v2` label-only configurations
  are not needed again and stay as the record of these sessions.
- **Spike sessions are never publishable** (item 8). Publication policy v1 refuses `spike-*`
  configuration ids as it refuses `experiment-*` ones (one prefix list,
  `scripts/lib/never-published-configurations.json`), `aggregate.mjs` refuses their records, and
  `configs/board.json` cannot name one. All 17 POCO F8 Ultra spike sessions and the six Galaxy S23
  memory-probe sessions read `publishable: false`, refusal "spike session".
- **The policy checks decode length** (items 2 and 8). New check `decode`: under `decodeMode: fixed`
  every repetition must decode `maxDecodeTokens`; otherwise the check states the count, and
  repetitions that stop at different lengths before the budget leave it unknown. The Gemma 3 OpenCL
  session now reads "every repetition decoded 16 of 128 tokens, stopped at the end of turn", and its
  row statement is "decode 16 of 128 tok, stopped at the end of turn"; the Gemma 4 sessions state
  85, 79 and 83. A row whose repetitions decoded different lengths states the range ("decode
  16–128 of 128 tok"), never the budget alone.
- **geniex-npu-log-v1 is confirmed** (item 5c): it left `PENDING_DEVICE_CONFIRMATION`, and the
  GENIE-X NPU session of bundle `8c41c4e258ff…` reads backend `confirmed for npu by the app log
  (geniex-npu-log-v1)` from the bundle alone. [geniex-inapp.md](geniex-inapp.md#device-run-poco-f8-ultra-model-refresh-spike)
  records the device run.
- **Captures wake the screen and dismiss a swipe keyguard** (item 9,
  [#27](https://github.com/a-ghorbani/xebench/issues/27)). `capture_app_run.py` sends
  `KEYCODE_WAKEUP`, then reads the keyguard state and dismisses a swipe lock with
  `wm dismiss-keyguard`. It never dismisses or enters anything on a secure lock: it stops with a
  clear error before the launch, as it does when the lock screen is still showing after the
  dismissal. It records what it did in the local manifest.
- **Done before this follow-up, in `f49f824`** (items 5a and 5b): `stage_geniex_bundle.py --push`
  sets the bundle directory to mode 2775 and checks it, and geniex-inapp.md states that the QAIRT
  pipeline prepends BOS 151643 for Qwen3 (520 prompt tokens on the NPU against 519).

## Board sessions: Galaxy S23, Pixel 9, POCO X8 Pro (2026-11-01)

The board configurations of [REFERENCE-RUNS.md](../REFERENCE-RUNS.md#model-refresh-board-configurations) for the
Galaxy S23 (seven; no llama.cpp Gemma 4 E2B) and the Pixel 9 (eight), CPU only, under the single-rerun rule. These are
board sessions (3 repetitions, 120 s cooldown), staged into the history only (`aggregate.mjs --stage
--allow-unlisted-configuration`); `configs/board.json` did not change in that pull request; the publication pull request ([#55](https://github.com/a-ghorbani/xebench/pull/55)) declares the sets that display them. The POCO X8 Pro ran nothing (below).

| Item | Value |
| --- | --- |
| APK | `xebench-experimental-release-3dff57e.apk` (main at `3dff57e`), SHA-256 `885ef843…c358`, checked against its `SHA256SUMS` before the install and on each phone after it (`base.apk`) |
| Galaxy S23 | `SM-S911B`, `BP4A.251205.006.S911BXXSAFZI1`, `MemTotal` 7,242,456 kB; USB power, battery protection on |
| Pixel 9 | `CP1A.260505.005`, `MemTotal` 11,848,700 kB; AC power |
| POCO X8 Pro | `2511FPC34G` (MT6899), HyperOS `OS3.0.304.0.WPJEUXM` (`BP2A.250605.031.A3`), `MemTotal` 7,497,792 kB; USB power |
| Files | Every file at its pin; SHA-256 checked on the host and on the phone after each push (`.partial` name, renamed only on a match). The Galaxy S23 kept the files of its October probe, each re-hashed on the phone |

**Pixel 9 storage rotation.** The Pixel 9 had 2.0 GB free. The app's files directory held 8.2 GB of files of
retired setups (Llama 3.2 1B Q4_0, Q4_K_M and Q8_0 GGUF; Qwen2.5 1.5B Q8_0 GGUF; both Qwen2.5 `.litertlm` files; the
Llama 3.2 SpinQuant `.pte` and its `tokenizer.json` and `tokenizer_config.json`); they were deleted, leaving 10 GB
free. The new set (about 14 GB) still did not fit, so the files went on per engine: the three GGUF files (5.4 GB),
then the three `.litertlm` files (4.2 GB) after deleting the GGUF files, then the two `.pte` files and their
tokenizers (4.6 GB) after deleting the `.litertlm` files. Each engine's sessions ran with only its own files on the
phone. The ExecuTorch files stay on the phone.

| Phone | Configuration | Session | Reps | Conditions | Gate | Prompt / decode tok | Prefill tok/s, median (range) | Decode tok/s, median (range) | Policy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Galaxy S23 | `cpu-reference-v4-qwen3` | `1793529132591-0-lgs4x3ev4f` | 3/3 | pass, charging satisfied | pass | 519 / 128 | 136.6 (135.7–136.7) | 20.2 (20.0–20.2) | verified |
| Galaxy S23 | `cpu-reference-v4-gemma3` | `1793529645716-0-n3uxkkjqcee` | 3/3 | pass, satisfied | pass | 519 / 128 | 271.7 (271.0–273.0) | 22.6 (22.5–22.7) | verified |
| Galaxy S23 | `litertlm-gemma4-cpu-v2` | `1793530063260-0-cazlp1ncq1k` | 3/3 | pass, satisfied | pass | 519 / 128 | 185.9 (185.6–185.9) | 22.6 (22.5–22.6) | verified |
| Galaxy S23 | `litertlm-qwen3-int4-cpu-v2` | `1793530513103-0-0corrg3f8ple` | 3/3 | pass, charging **violated** | pass | 519 / 128 | 49.6 (49.5–49.7) | 8.6 (8.5–8.6) | failed (charging) |
| Galaxy S23 | `litertlm-gemma3-int4-cpu-v2` | `1793530981456-0-3bor5ypjkk5` | 3/3 | pass, satisfied | pass | 519 / 128 | 221.6 (221.0–221.7) | 38.0 (38.0–38.0) | verified |
| Galaxy S23 | `executorch-gemma4-cpu-v1` | `1793531968376-0-nm8f4rb5qbg` | 3/3 | pass, satisfied | pass | 519 / 128 | 53.4 (51.9–54.4) | 6.7 (6.6–6.7) | verified |
| Galaxy S23 | `executorch-qwen3-cpu-v1` | `1793532460521-0-chuv3do03n` | 3/3 | pass, satisfied | pass | 519 / 128 | 159.8 (158.4–161.7) | 23.3 (23.2–23.6) | verified |
| Pixel 9 | `cpu-reference-v4-qwen3` | `1793529641396-0-bn5bbeig96w` | 3/3 | pass, satisfied | pass | 519 / 128 | 150.8 (150.4–151.7) | 17.7 (17.5–17.7) | verified |
| Pixel 9 | `cpu-reference-v4-gemma3` | `1793530066282-0-pegxdqk8lqm` | 3/3 | pass, satisfied | pass | 519 / 128 | 302.1 (301.5–302.9) | 19.3 (19.2–19.3) | verified |
| Pixel 9 | `cpu-reference-v4-gemma4` | `1793530497451-0-7eepgm52wev` | 3/3 | pass, satisfied | pass | 519 / 128 | 93.5 (93.2–94.3) | 11.9 (11.9–12.0) | verified |
| Pixel 9 | `litertlm-gemma4-cpu-v2` | `1793531082994-0-uq07mqfaa3` | 3/3 | pass, satisfied | pass | 519 / 128 | 176.9 (176.8–178.1) | 19.3 (19.2–19.3) | verified |
| Pixel 9 | `litertlm-qwen3-int4-cpu-v2` | `1793531535530-0-8f2vg3uxj1m` | 3/3 | pass, satisfied | pass | 519 / 128 | 51.6 (50.9–51.9) | 6.7 (6.6–6.7) | verified |
| Pixel 9 | `litertlm-gemma3-int4-cpu-v2` | `1793532024743-0-q5iwrhjqcuo` | 3/3 | pass, satisfied | pass | 519 / 128 | 205.2 (196.4–205.7) | 26.1 (25.1–26.3) | verified |
| Pixel 9 | `executorch-gemma4-cpu-v1` | `1793532613003-0-zz0b0x2uzag` | 3/3 | pass, satisfied | pass | 519 / 128 | 45.0 (44.5–45.1) | 4.9 (4.9–5.0) | verified |
| Pixel 9 | `executorch-qwen3-cpu-v1` | `1793533133727-0-fowesvfu7hd` | 3/3 | pass, satisfied | pass | 519 / 128 | 159.6 (155.0–161.2) | 18.3 (16.8–18.4) | verified |

- **The llama.cpp fixed decode works on the phones.** Every llama.cpp repetition decoded 128 tokens past the end of
  turn: `decodeTokens` 128, and decode tok/s is 127 tokens over `predicted_ms` (llama.cpp's `predicted_n` 127).
  `effectiveSettings.completion.logit_bias` lists each model's end-of-generation ids with `false` (Qwen3: 128247,
  151643, 151645, 151662, 151663, 151664; Gemma 3: 1, 106, 212; Gemma 4: 1, 50, 106), and `effectiveSettings.init`
  has `state_cache_budget_mb` 0. With the cache off, llama.cpp counts 519 prompt tokens for the Gemma models too.
  The Galaxy S23 `cpu-reference-v4-qwen3` session ran first, as the check, before the others started.
- **No rerun was needed:** every first session had 3 of 3 repetitions, condition verdict `pass` and gate `pass`.
- **Galaxy S23 LiteRT-LM Qwen3 1.7B: charging violated.** In the third repetition the battery went from 96 % to 95 %,
  and the phone reported `CHARGING` in 5 of 27 samples: on USB power it starts charging again at 95 %. The
  single-rerun rule as it stood when the session ran did not cover charging, so it got no rerun; publication
  policy v1 rates it `failed` (charging). The rule now also reruns a session whose charging check is not
  `satisfied` (owner decision 2026-11-01, [REFERENCE-RUNS](../REFERENCE-RUNS.md#model-refresh-board-configurations)),
  so this setup gets one unchanged rerun on the same phone and APK. The next session's first readiness wait took 542 s (107 of 108 polls not ready: `charging`).
- The Pixel 9 LiteRT-LM Gemma 3 1B session's first repetition was 4.5 % slower than the other two (prefill 196.4
  against 205.2 and 205.7), and the ExecuTorch Qwen3 1.7B session's first repetition decoded 8.7 % slower (16.8
  against 18.3 and 18.4). In every other session the three repetitions stay within 4.6 % of each other on both
  rates.
- Gemma 4 E2B memory, app PSS after the repetition (max RSS during it): llama.cpp on the Pixel 9 3863 MiB (3916);
  LiteRT-LM 1456–1480 (1516) on the Galaxy S23 and 1598–1619 (1671) on the Pixel 9; ExecuTorch 2169–2172 (2901) on
  the Galaxy S23 and 2251–2259 (2306) on the Pixel 9. No session was killed.

### POCO X8 Pro: not yet measured

No session ran on the POCO X8 Pro: the app could not be installed. On HyperOS, `adb install` of the experimental APK
fails with `INSTALL_FAILED_USER_RESTRICTED` ("Install canceled by user"). The phone's log shows HyperOS's own USB
install confirmation (`com.miui.securitycenter/com.miui.permcenter.install.AdbInstallActivity`), so each install over
USB waits for someone to confirm it on the phone, or for "Install via USB" to be enabled in the developer options.
The phone runs a swipe keyguard (not secure), which the capture's own dismissal handles. Before the first X8 Pro
session, someone has to approve the install on the phone. After that the phone needs no file rotation (195 GB free),
and the risk to watch is llama.cpp Gemma 4 E2B on its 7.5 GB of RAM: on the Galaxy S23 (7.2 GB) lmkd ended the app
while it loaded the same file.

## Board sessions: POCO F8 Ultra (2026-11-01)

The board configurations of [REFERENCE-RUNS.md](../REFERENCE-RUNS.md#model-refresh-board-configurations) for the
POCO F8 Ultra: all thirteen, CPU, OpenCL and NPU, under the single-rerun rule. These are board sessions (3
repetitions, 120 s cooldown), staged into the history only (`aggregate.mjs --stage --allow-unlisted-configuration`);
`configs/board.json` did not change in that pull request; the publication pull request ([#55](https://github.com/a-ghorbani/xebench/pull/55)) declares the sets that display them.

| Item | Value |
| --- | --- |
| APKs | `xebench-experimental-release-3dff57e.apk`, SHA-256 `885ef843…c358`, and `xebench-geniex-release-3dff57e.apk`, SHA-256 `ff3ee310…8cbd` (main at `3dff57e`, clean); each checked against its `SHA256SUMS` before the install and on the phone after it (`base.apk`) |
| APK order | experimental at 10:25 UTC (twelve configurations); GENIE-X at 12:35 (`geniex-qwen3-sm8850-npu-v1`); experimental again at 12:49, left installed |
| POCO F8 Ultra | `25102PCBEG` (SM8850), `MemTotal` 11,286,132 kB; AC power, battery `FULL` throughout |
| Files | The files of the October spike, each pinned file re-hashed on the phone before the first session; the GENIE-X bundle verified in place by `stage_geniex_bundle.py --push` (mode 2775, nothing pushed) |

`cpu-reference-v4-qwen3` ran first, as the check of the llama.cpp fixed decode, before the others started.

| Configuration | Run | Session | Reps | Conditions | Gate | Prompt / decode tok | Prefill tok/s, median (range) | Decode tok/s, median (range) | Policy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu-reference-v4-qwen3` | llama.cpp CPU | `1793528833103-0-lbkaqccwro` | 3/3 | pass, satisfied | pass | 519 / 128 | 203.4 (201.9–203.7) | 35.6 (35.6–35.7) | verified |
| `cpu-reference-v4-gemma4` | llama.cpp CPU | `1793529262255-0-5q39sg7gtsf` | 3/3 | pass, satisfied | pass | 519 / 128 | 129.8 (117.6–142.7) | 26.7 (26.6–26.7) | verified |
| `cpu-reference-v4-gemma3` | llama.cpp CPU | `1793529671406-0-kejv04w7u1` | 3/3 | pass, satisfied | pass | 519 / 128 | 404.7 (401.4–405.8) | 50.3 (50.2–50.5) | verified |
| `llamacpp-gemma4-opencl-hexagon-v1` | llama.cpp CPU (gate reference) | `1793530881477-2-3h0q1d0taof` | 3/3 | pass, satisfied | pass | 519 / 128 | 141.2 (126.6–142.9) | 26.8 (26.8–26.8) | verified |
| `llamacpp-gemma4-opencl-hexagon-v1` | llama.cpp OpenCL | `1793530464960-1-w452032gqz` | 3/3 | pass, satisfied | pass | 519 / 128 | 400.4 (397.3–401.7) | 29.4 (29.2–29.8) | verified |
| `llamacpp-gemma4-opencl-hexagon-v1` | llama.cpp Hexagon | `1793530071131-0-m7ydqxrk9wh` | 3/3 | pass, satisfied | pass | 519 / 128 | 1688.3 (1681.1–1733.3) | 31.2 (30.1–31.3) | verified |
| `llamacpp-qwen3-opencl-hexagon-v1` | llama.cpp CPU (gate reference) | `1793531694897-1-m1hckbaqmzq` | 3/3 | pass, satisfied | pass | 519 / 128 | 203.5 (202.9–203.6) | 35.5 (35.5–35.7) | verified |
| `llamacpp-qwen3-opencl-hexagon-v1` | llama.cpp OpenCL | `1793531294541-0-qo9a0qiaoli` | 3/3 | pass, satisfied | pass | 519 / 128 | 415.9 (414.7–416.0) | 27.1 (26.9–29.8) | verified |
| `llamacpp-qwen3-opencl-hexagon-v1` | llama.cpp Hexagon | `1793532077408-2-ki7wxv0zx7` | 3/3 | pass, satisfied | pass | 519 / 128 | 2514.1 (2499.6–2542.1) | 45.1 (43.7–45.4) | verified |
| `llamacpp-gemma3-opencl-hexagon-v1` | llama.cpp CPU (gate reference) | `1793533222721-2-9h9ffjxra69` | 3/3 | pass, satisfied | pass | 519 / 128 | 405.5 (404.4–406.0) | 50.6 (50.6–50.8) | verified |
| `llamacpp-gemma3-opencl-hexagon-v1` | llama.cpp OpenCL | `1793532838998-1-8njmz5i94c6` | 3/3 | pass, satisfied | pass | 519 / 128 | 966.2 (946.2–971.2) | 42.1 (41.6–42.1) | verified |
| `llamacpp-gemma3-opencl-hexagon-v1` | llama.cpp Hexagon | `1793532463356-0-k0dqkk33548` | 3/3 | pass, satisfied | pass | 519 / 128 | 3634.7 (3634.4–3716.0) | 46.6 (45.8–47.2) | verified |
| `litertlm-gemma4-cpu-v2` | LiteRT-LM CPU | `1793533611266-0-joii7rfsgih` | 3/3 | pass, satisfied | pass | 519 / 128 | 259.3 (259.0–260.0) | 29.7 (29.4–29.7) | verified |
| `litertlm-qwen3-int4-cpu-v2` | LiteRT-LM CPU | `1793534024008-0-vrm9j74lr98` | 3/3 | pass, satisfied | pass | 519 / 128 | 91.7 (91.5–91.9) | 10.6 (10.6–10.7) | verified |
| `litertlm-gemma3-int4-cpu-v2` | LiteRT-LM CPU | `1793534450540-0-6rbrh8x22cs` | 3/3 | pass, satisfied | pass | 519 / 128 | 661.7 (650.7–661.7) | 42.2 (42.0–42.2) | verified |
| `litertlm-gemma3-sm8850-npu-v2` | LiteRT-LM CPU (gate reference) | `1793535204332-1-fyi6dc2vmz` | 3/3 | pass, satisfied | pass | 519 / 128 | 661.6 (655.5–668.5) | 42.1 (42.1–42.5) | verified |
| `litertlm-gemma3-sm8850-npu-v2` | LiteRT-LM NPU | `1793534832599-0-agxjwf3zgng` | 3/3 | pass, satisfied | pass | 519 / 128 | 5010.7 (4859.7–5081.4) | 68.8 (67.9–69.2) | verified |
| `executorch-gemma4-cpu-v1` | ExecuTorch CPU | `1793535622764-0-ktcp60m895r` | 3/3 | pass, satisfied | pass | 519 / 128 | 66.6 (66.3–66.6) | 6.9 (6.8–7.0) | verified |
| `executorch-qwen3-cpu-v1` | ExecuTorch CPU | `1793536089765-0-scjybiccsg` | 3/3 | pass, satisfied | pass | 519 / 128 | 323.0 (319.6–323.8) | 31.3 (30.9–31.7) | verified |
| `geniex-qwen3-sm8850-npu-v1` | GENIE-X CPU (gate reference) | `1793536545040-0-xf8twobz2gk` | 3/3 | pass, satisfied | pass | 519 / 128 | 155.7 (155.4–155.8) | 34.3 (33.5–34.3) | verified |
| `geniex-qwen3-sm8850-npu-v1` | GENIE-X NPU | `1793536930813-1-p8yctwh7tum` | 3/3 | pass, satisfied | pass | 520 / 128 | 2297.1 (2246.9–2680.9) | 27.9 (27.4–29.3) | verified |

- **No rerun was needed:** every first session had 3 of 3 repetitions, condition verdict `pass` (charging satisfied)
  and gate `pass`, and publication policy v1 rates all 21 `verified`. Backends: OpenCL and Hexagon `confirmed` from
  llama.rn's device report, the LiteRT-LM NPU from the app's log (litert-npu-log-v1), the GENIE-X NPU from the app's
  log (geniex-npu-log-v1), ExecuTorch from the program's delegates; the CPU runs are `consistent`.
- **Decode length:** 128 tokens in every repetition. llama.cpp (fixed decode: `predicted_n` 127, the end-of-generation
  `logit_bias` and `state_cache_budget_mb` 0 recorded, as on the Galaxy S23 and Pixel 9), LiteRT-LM and ExecuTorch
  decode a fixed 128; GENIE-X, which stops at the end of turn, reached the 128-token budget on every repetition.
- **Prompt tokens:** 519 everywhere, the Gemma models on llama.cpp included; 520 on the GENIE-X NPU, whose QAIRT
  pipeline prepends a BOS (`<|endoftext|>`) for Qwen3.
- **LiteRT-LM 0.18.1 decodes Gemma 3 1B more slowly on the CPU than 0.18.0 did.** Three 0.18.1 sessions decode at
  42.0 (the spike's NPU-configuration CPU reference, 2026-10-22), 42.2 (`litertlm-gemma3-int4-cpu-v2`) and 42.1 tok/s
  (the CPU reference of `litertlm-gemma3-sm8850-npu-v2`), against 47.4 and 48.1 in the two 0.18.0 sessions behind
  the current board (bundle [`019da4ad8c6d…`](../../evidence/019da4ad8c6dc3cb843f07d5b5898ea78a603ad156ab95bc7c545e83770860d6/)):
  about 12 % slower. Prefill is 661.6–661.7 against 691.2 and 693.8 (about 4.5 % slower).
- **The LiteRT-LM NPU is faster than its 0.18.0 session:** 5010.7 / 68.8 tok/s against 4578.9 / 56.9 (the spike's
  0.18.1 check gave 4725.5 / 57.6).
- **Spread.** The slowest repetition is 9 % or more below the fastest in four places:
  - prefill of the GENIE-X NPU, 16.2 % (the first repetition 2680.9, the other two 2297.1 and 2246.9);
  - prefill of `cpu-reference-v4-gemma4`, 17.6 % (142.7, 117.6, 129.8), and of the Gemma 4 E2B CPU gate reference in
    `llamacpp-gemma4-opencl-hexagon-v1`, 11.4 % (142.9, 141.2, 126.6);
  - decode of Qwen3 1.7B on OpenCL, 9.7 % (27.1, 26.9, 29.8: the third repetition is the fast one).

  The LiteRT-LM NPU prefill varies by 4.4 % (4859.7–5081.4) and the GENIE-X NPU decode by 6.4 % (27.4–29.3). Every
  other rate stays within 4.1 % across its three repetitions.

## Evidence

| Bundle | Sessions | What |
| --- | --- | --- |
| [`31f08310a8de…`](../../evidence/31f08310a8de323504572c42bc4b924ffe81f6b79c8a52a5707dc4acb0825f37/) | 6 | Galaxy S23 memory probe: LiteRT-LM and ExecuTorch Gemma 4 E2B, llama.cpp Gemma 4 E2B with mmap, Qwen3 1.7B on three engines |
| [`49a216f37945…`](../../evidence/49a216f37945edcc80b5d31a981206c1984e3293f16e4e47abd78adad2fe69e8/) | 2 | Section 1: `litertlm-gemma3-sm8850-npu-v2` on LiteRT-LM 0.18.1 (NPU and its CPU reference) |
| [`c12fb048be9b…`](../../evidence/c12fb048be9bfbf75361b33b57c3fd158510a84c45c369d4c35b07410c7b3880/) | 13 | Sections 2–4: the three llama.cpp configurations, LiteRT-LM Qwen3 (`-v1`), LiteRT-LM Gemma 4 and both ExecuTorch (`-v2`) |
| [`8c41c4e258ff…`](../../evidence/8c41c4e258ff4dd1fc149f165b8c45c7fe2cc3bb91f1db13b2db2acf078befb5/) | 4 | Section 5: both GENIE-X captures (the refused first attempt and the rerun) |
| [`56707acb5d2e…`](../../evidence/56707acb5d2eafacda4262e3c0d504d08e0e517fec177acecfabf9bd86bf5ac7/) | 1 | Board sessions: Galaxy S23 `cpu-reference-v4-qwen3` |
| [`d9d6990851d5…`](../../evidence/d9d6990851d52602fd1fe8ad46ef624fb319e9ce38204ea9437ea6a105a3d73e/) | 1 | Board sessions: Galaxy S23 `cpu-reference-v4-gemma3` |
| [`cddf53ba3a5b…`](../../evidence/cddf53ba3a5b455090c6b7219874451f65b1bf656f1d542d582e17a89b315915/) | 1 | Board sessions: Galaxy S23 `litertlm-gemma4-cpu-v2` |
| [`2fbe08f18d55…`](../../evidence/2fbe08f18d553e0097a46cbfa3ee969f0f76af6dae04fd65d5b16bfa5b3a8403/) | 1 | Board sessions: Galaxy S23 `litertlm-qwen3-int4-cpu-v2` |
| [`2b94453b4d1d…`](../../evidence/2b94453b4d1d0b0bf22c74a0046db64978008d96e51f86bed1218b22a8fcc522/) | 1 | Board sessions: Galaxy S23 `litertlm-gemma3-int4-cpu-v2` |
| [`2859a75aa721…`](../../evidence/2859a75aa721d35c567ddddc02931eaa0fbdf9bdeb0afbf404538a0b8114654e/) | 1 | Board sessions: Galaxy S23 `executorch-gemma4-cpu-v1` |
| [`dd9d2a9837cd…`](../../evidence/dd9d2a9837cd622e6077345d9553d78952b372278ad8c756bb6d8945d9e0eff4/) | 1 | Board sessions: Galaxy S23 `executorch-qwen3-cpu-v1` |
| [`dc2c59056874…`](../../evidence/dc2c590568743747c936b436cba0debae0c8a602074a909705bf72ae0f93f9a2/) | 1 | Board sessions: Pixel 9 `cpu-reference-v4-qwen3` |
| [`4e6178a5e21b…`](../../evidence/4e6178a5e21b5689298fc830a1d91a7f48afa7c578010acbecbf428e979e982b/) | 1 | Board sessions: Pixel 9 `cpu-reference-v4-gemma3` |
| [`3f8f3138ef5f…`](../../evidence/3f8f3138ef5f063e4d53d307c0de54613e0c68ea38b00104f8640ee89f44f1d4/) | 1 | Board sessions: Pixel 9 `cpu-reference-v4-gemma4` |
| [`5df4b166f5ff…`](../../evidence/5df4b166f5ffa11f0846ddb6b3073a93eed0b9c7ef6b5655fee9015b1d460d60/) | 1 | Board sessions: Pixel 9 `litertlm-gemma4-cpu-v2` |
| [`6b9e6a3bec98…`](../../evidence/6b9e6a3bec98b1f015f291daac5b832a375cf8c05a0a3a8d097f8c43147cc47d/) | 1 | Board sessions: Pixel 9 `litertlm-qwen3-int4-cpu-v2` |
| [`5ffecc68b9a4…`](../../evidence/5ffecc68b9a4a0a5eeca8bd8fe86c2d18272f93132636aa8205ddc4934fc37ac/) | 1 | Board sessions: Pixel 9 `litertlm-gemma3-int4-cpu-v2` |
| [`8041a2063637…`](../../evidence/8041a206363725e7fe077fb570469f132eeabe9ddb6554266cb5a0e7fd83ffe9/) | 1 | Board sessions: Pixel 9 `executorch-gemma4-cpu-v1` |
| [`87faa66cd405…`](../../evidence/87faa66cd4055d832c69853bddd823ca1236221d8e84508ccbec377f343f2b15/) | 1 | Board sessions: Pixel 9 `executorch-qwen3-cpu-v1` |
| [`4962248f3874…`](../../evidence/4962248f3874aa41ef01e2f4efed34f0f02c691bc72db295f594711299678a7d/) | 1 | Board sessions: POCO F8 Ultra `cpu-reference-v4-qwen3` |
| [`d4868653a047…`](../../evidence/d4868653a0473672942213ae4212aba783457b77676198235eaa8f81d639ac56/) | 1 | Board sessions: POCO F8 Ultra `cpu-reference-v4-gemma4` |
| [`b8d1aaa9922e…`](../../evidence/b8d1aaa9922e05870eebe9ec0e8acb66e7440d0333d233f26dcb15f653d63dfe/) | 1 | Board sessions: POCO F8 Ultra `cpu-reference-v4-gemma3` |
| [`658799957eef…`](../../evidence/658799957eefe03b73e298dc8cc43ef25d28cee8ae6d48747d3d87b46c527359/) | 3 | Board sessions: POCO F8 Ultra `llamacpp-gemma4-opencl-hexagon-v1` |
| [`57c07c4b15a3…`](../../evidence/57c07c4b15a376c8002c9ec22a7052c1701a513b1fb3dde7fbcc024261e353d4/) | 3 | Board sessions: POCO F8 Ultra `llamacpp-qwen3-opencl-hexagon-v1` |
| [`54b1832f9dde…`](../../evidence/54b1832f9dde54066fcd60ebd99d1bda43793b8b1e24bf2743d780f197522f90/) | 3 | Board sessions: POCO F8 Ultra `llamacpp-gemma3-opencl-hexagon-v1` |
| [`cc5962fa2ca2…`](../../evidence/cc5962fa2ca2d6d56a58f7deccd6836f1c204e504e805582f34e5111a9d7a708/) | 1 | Board sessions: POCO F8 Ultra `litertlm-gemma4-cpu-v2` |
| [`73ec19b319eb…`](../../evidence/73ec19b319eb0e8b354ce213c556d148a8cd4d5c238b3c6368d0257e076d99f5/) | 1 | Board sessions: POCO F8 Ultra `litertlm-qwen3-int4-cpu-v2` |
| [`4ce716f908ac…`](../../evidence/4ce716f908ac44d3aac27b4f9d219abd749f107dc27cc35014547d77e0f84417/) | 1 | Board sessions: POCO F8 Ultra `litertlm-gemma3-int4-cpu-v2` |
| [`9f004158ed59…`](../../evidence/9f004158ed598ba26cc1f190ab12e048e92cf321404ed3e96d6673a91c6037b0/) | 2 | Board sessions: POCO F8 Ultra `litertlm-gemma3-sm8850-npu-v2` |
| [`27286631777c…`](../../evidence/27286631777c4bd7b970f2aaa6de8ca1ceb9d1d2bd62169a78b1337f668516d2/) | 1 | Board sessions: POCO F8 Ultra `executorch-gemma4-cpu-v1` |
| [`01a7fe322fe4…`](../../evidence/01a7fe322fe4532da207c191cc46b755bc9fc8bc9df022402201777dafa143e6/) | 1 | Board sessions: POCO F8 Ultra `executorch-qwen3-cpu-v1` |
| [`304967e2f895…`](../../evidence/304967e2f895d90edc289151f4934bfc721bcd4f6eac7c14685c5e6b269009a2/) | 2 | Board sessions: POCO F8 Ultra `geniex-qwen3-sm8850-npu-v1` |

The three `-v1` captures with unexportable labels (LiteRT-LM Gemma 4, ExecuTorch Gemma 4 and Qwen3) stay local.
