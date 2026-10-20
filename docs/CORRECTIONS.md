# Corrections log

Public copy (README, ENGINES.md, METHODOLOGY.md, `data/findings.json`) describes the current state
only. When a published claim turns out to be wrong, the copy is rewritten to the correct current
facts, a short dated label is left where a reader needs it ("Corrected <date>", or
`context.correctedAt` in a finding), and the history goes here. See [STYLE.md](./STYLE.md).

Newest first.

## 2026-10-20 — LiteRT-LM board rows measured on the board prompt with LiteRT-LM 0.18.0

- **What the board showed.** Five July 2026 LiteRT-LM CPU rows without versions, each labelled
  "n=3 cold-run median; prompt std-pp512-v1; decode 128 tok": Qwen2.5 1.5B Q8 on the Pixel 9
  (77.3 prefill / 8.2 decode tok/s), the POCO F8 Ultra (323.8 / 24.2) and the Galaxy S23
  (78.6 / 16.0); Gemma 3 1B int4 on the POCO F8 Ultra (691.7 / 40.6) and the Galaxy S23
  (186.6 / 36.1).
- **What is true.** Those rows came from LiteRT-LM 0.14.0's built-in `benchmark()` call (the July
  adapter, `LiteRtAdapter.ts` in commit `88433da`). It prefills 512 synthetic tokens, not the
  std-pp512-v1 prompt, and then decodes 128 steps; the rows did not measure the std-pp512-v1 text.
  Every LiteRT-LM lab row is now measured with LiteRT-LM 0.18.0 under the current run conditions, on
  the std-pp512-v1 prompt prefilled verbatim, decoding exactly 128 tokens with the end of turn
  ignored, and passing the output-correctness gate:

  | Phone | Model | Backend | Prefill / decode tok/s |
  | --- | --- | --- | --- |
  | POCO F8 Ultra | Gemma 3 1B int4 | CPU | 691.2 / 47.4 |
  | POCO F8 Ultra | Gemma 3 1B int4 (SM8850 bundle) | NPU | 4578.9 / 56.9 |
  | POCO F8 Ultra | Qwen2.5 1.5B Q8 | CPU | 330.3 / 25.5 |
  | Galaxy S23 | Gemma 3 1B int4 | CPU | 228.1 / 38.6 |
  | Galaxy S23 | Qwen2.5 1.5B Q8 | CPU | 112.1 / 18.2 |
  | Pixel 9 | Qwen2.5 1.5B Q8 | CPU | 111.7 / 12.2 |

  The NPU row's backend is confirmed from the app's own log
  ([RUN-CONDITIONS.md](./RUN-CONDITIONS.md#litert-lm-npu-backend-confirmed-by-the-apps-log-litert-npu-log-v1)).
  The finding `litert-lm-npu-integration` states that the POCO F8 Ultra NPU row is on the board.
- **What changed.** `configs/board.json` declares the LiteRT-LM 0.18.0 set
  (`litertlm-gemma3-int4-cpu-v2` and `litertlm-qwen25-q8-cpu-v2` on `cpu`,
  `litertlm-gemma3-sm8850-npu-v2` on `npu`), measured from 2026-10-20. The five July rows are
  replaced; `data/sessions/index.json` keeps them, with their month and its basis, under
  `replacedRows`, and keeps the sessions of the `-v1` configurations (the same runs without the
  fixed decode length) and the NPU configuration's CPU reference run as history.

## 2026-10-19 — llama.cpp board rows measured with llama.rn 0.13.0-rc.7

- **What the board showed.** Nine July 2026 llama.cpp rows without versions next to nine rc.7
  CPU rows: Qwen2.5 1.5B Q8_0 on CPU on the Pixel 9, OnePlus 6, F8 Ultra and S23; Gemma 3 1B
  Q4_0 (QAT) on CPU on the F8 Ultra and S23; Llama 3.2 1B Q4_K_M on CPU on the OnePlus 6; and
  Qwen2.5 1.5B Q8_0 on the OpenCL GPU on the F8 Ultra and S23.
- **What is true.** Every llama.cpp board row is measured with llama.cpp b11385 (bf9a0cc) and
  llama.rn 0.13.0-rc.7 under the current run conditions: 13 CPU rows, the F8 Ultra OpenCL GPU
  row and the F8 Ultra Hexagon NPU row (Llama 3.2 1B Q4_K_M). Four July setups have no row: the
  OnePlus 6 (two setups) cannot complete the run conditions (sustained thermal throttling); on the
  S23 the OpenCL run is killed for low memory; and on the S23, Qwen2.5 1.5B Q8_0 on CPU is not
  stable within a session (prefill falls across repetitions), so it waits for a stability check
  in the run conditions ([#40](https://github.com/a-ghorbani/xebench/issues/40)).
- **What changed.** `configs/board.json` adds `cpu-reference-v3-qwen25`,
  `cpu-reference-v3-gemma3` (CPU), `llamacpp-qwen25-opencl-v1` (GPU) and
  `llamacpp-llama32-q4km-hexagon-v1` (NPU) to the llama.cpp set, lists the four setups in
  `retiredKeys` with their reasons, and declares no `carryover`. Five July rows are replaced by
  rc.7 rows and four setups are retired; `data/sessions/index.json` keeps all nine July rows,
  with their month and its basis, under `replacedRows`, and keeps the S23 Qwen2.5 CPU session.

## 2026-10-19 — llama.cpp Hexagon row leaves the board

- **What the board showed.** The llama.cpp NPU row (POCO F8 Ultra, Llama 3.2 1B Q4_0, 197.6
  prefill / 38.6 decode tok/s) with `validation: "failed"` (see the 2026-10-17/18 entry below),
  and the finding `llama-cpp-hexagon-output`, headed "one result marked failed", explaining it.
- **What is true.** The board shows only each engine's latest measurement set and never a failed
  row ([RUN-CONDITIONS.md, Measurement sets](./RUN-CONDITIONS.md#measurement-sets)). The Q4_0
  setup is not measured again: its CPU reference misses a correctness probe, so the llama.cpp NPU
  setup is Llama 3.2 1B Q4_K_M with llama.rn 0.13.0-rc.7, a new key. No llama.cpp NPU row is on
  the board.
- **What changed.**
  - `configs/board.json` lists the row's key in `retiredKeys` ("replaced by Q4_K_M; Q4_0 CPU
    reference misses a correctness probe", 2026-10-19). `data/benchmarks.json` no longer holds
    the row; `data/sessions/index.json` keeps it byte for byte, with its numbers and correction
    note, under `replacedRows` (`reason: "retired"`). `scripts/correct_hexagon_row.mjs --check`
    finds it there.
  - The finding `llama-cpp-hexagon-output` is removed from `data/findings.json`; the board does
    not explain a row it does not show. ENGINES.md and README state that the board has no llama.cpp
    NPU row.
  - The 20 July 2026 rows without versions carry their month (`sourceDate` 2026-07) and its basis
    (`dateBasis`): 18 from their run records, 2 from the first commit carrying them
    (`scripts/date_legacy_rows.mjs`). No version is backfilled.

## 2026-10-19 — Board labels and vendor notes

- **What the board showed.** Labels in several spellings for the same thing: LiteRT-LM `q8`, SoC
  `sdm845`, `"not stated"` as a quant, quants carrying extra detail (`4-bit groupwise (g=128)`,
  `BF16 (unquantized)`, `int4 QAT (529MB, ctx 2048)`), devices `Samsung S26 Ultra` and
  `iPhone 17 Pro`, and vendor notes in lab jargon (XNNPACK, KleidiAI, cpufreq governor, adb).
  It also showed a vendor row for a laptop browser: LiteRT-LM GPU, MacBook Pro (M4 Max, platform
  `macos`), Gemma 4 E2B, 76 tok/s decode via WebGPU in a browser, Google Developers Blog
  (LiteRT-LM), 2026.
- **What is true.** The labels name the same quants, chips and phones; only the spelling changes.
  The board compares phones, and the MacBook figure is a browser deployment, not a phone or a
  native app.
- **What changed.** `data/benchmarks.json` uses one vocabulary: `Q8`, `SDM845`, no quant when the
  source states none, `4-bit groupwise`, `BF16`, `4-bit QAT`, `4-bit QAT + LoRA` (was
  `QAT+LoRA 4-bit`), `Samsung Galaxy S26 Ultra`,
  `Apple iPhone 17 Pro`; each vendor note is one plain sentence with the source's meaning. The
  MacBook Pro row is dropped from the data (its values are above). Lab rows name their source
  "xebench" (was "PocketPal R&D lab (xebench)") and every row whose phone ships with one chip
  names it (`chipName`, e.g. "Snapdragon 8 Elite Gen 5"); `scripts/migrate_board_labels.mjs`
  applies both to the board rows and to the published session rows they equal. The LiteRT-LM NPU
  finding names the issue in words ("a known LiteRT-LM issue"); the issue link stays in its
  sources. Every row whose quant names a certain bit width carries it (`quantBits`, contract 1.3),
  so the board can show "4-bit" next to the exact scheme. The Arm source is titled "Arm Learning
  Path (ExecuTorch tutorial)". Dropped quant details:
  group size 128 and the Gemma 3 bundle's 529 MB size and 2048-token context, which do not change
  how a row compares. `scripts/check_public_text.mjs` enforces the vocabulary in CI.

## 2026-10-17/18 — llama.cpp Hexagon board row and "functional" claim

PR [#25](https://github.com/a-ghorbani/xebench/pull/25).

- **What we published.** ENGINES.md described llama.cpp's Hexagon backend (`ggml-hexagon`) as
  functional and said it "lost to the CPU" on the Snapdragon 8 Elite Gen 5. The board showed the
  llama.cpp NPU row (POCO F8 Ultra, Llama 3.2 1B Q4_0, 197.6 prefill / 38.6 decode tok/s, n=3,
  `std-pp512-v1`) as a normal legacy measurement. That row had no output check.
- **What a re-check found (2026-10-17).** With llama.rn 0.12.4 (the version our harness locked
  when the row was measured; the July build's own version was not recorded), the Hexagon output on
  the F8 Ultra is wrong: Llama 3.2 1B (Q4_K_M and Q4_0) degenerates on every tested prompt longer
  than about 128 tokens, and Gemma 3 1B fails even the short prompt. Evidence bundles
  `7cd4368bf08f339b78e731c2eed789768de44f736f338ad38f06ee154c3ae478` (Llama 3.2) and
  `c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972` (Gemma 3).
- **llama.rn 0.13.0-rc.7 (2026-10-18).** On the same phone the Hexagon output is correct for
  Llama 3.2 1B (Q4_K_M passes; Q4_0 passes the same probes as its CPU reference, gate unverified;
  bundle `20009fec0a56fe4bcb2b0046a2e160e3fca5c9263de1fef8a364dc62674ca210`) and for Gemma 3 1B
  (every probe passes; bundle `beb9a67ced8709bf195a64d048be0d8529505f5b76e24ee612de2d8867046bd2`).
- **What changed.**
  - `data/benchmarks.json`: the row's `validation` is `failed` (was `legacy`) and a dated note is
    appended to its `asStated`; its numbers are kept. Applied by `scripts/correct_hexagon_row.mjs`
    and pinned by `tests/test_hexagon_correction.py`.
  - `scripts/aggregate.mjs --update` refuses to overwrite or downgrade a `failed` row unless
    `--replace-failed` is given.
  - New finding `llama-cpp-hexagon-output`. ENGINES.md and README describe the Hexagon output by
    llama.rn version. The GENIE-X "beats llama.cpp CPU/HTP" comparison drops "/HTP", which relied
    on the failed row; README's "NPU is not automatically faster" bullet names Genie.
  - Successor: a separate Q4_K_M row (the pinned reference model), not yet measured.
- **Details:** "Legacy Hexagon row check" and "llama.rn 0.13.0-rc.7 re-check" in
  [docs/experiments/litert-accelerators.md](./experiments/litert-accelerators.md).

## 2026-10-17 — LiteRT-LM NPU and GPU

PR [#18](https://github.com/a-ghorbani/xebench/pull/18); copy rewritten to present tense in
PR [#25](https://github.com/a-ghorbani/xebench/pull/25).

- **What we published (July 2026).**
  1. LiteRT-LM's Qualcomm NPU path was "blocked upstream" because Google withholds two prebuilt
     bridge libraries, `libLiteRtDispatch_Qualcomm.so` and `libGemmaModelConstraintProvider.so`
     (finding `litert-lm-npu-blocked`).
  2. The NPU run aborted (SIGABRT) on a missing Hexagon v73 QNN skel.
  3. LiteRT GPU (ML Drift) "can't find OpenCL", because an app cannot load the vendor
     `libOpenCL.so` on stock Android.
- **What is true (device re-evaluation 2026-10-17, Galaxy S23 and POCO F8 Ultra).**
  1. Both libraries were already public in July. Per-architecture dispatch libraries are attached
     to LiteRT GitHub releases (`litert_npu_runtime_libraries.zip`; v73 since v2.0.0-alpha,
     2025-05; v81 since v2.1.0rc1, 2025-11) and are on Google Maven as
     `com.google.ai.edge.litert:litert-npu-runtime-qualcomm` (2.3.0 published 2026-10-06).
     `libGemmaModelConstraintProvider.so` has been committed in the LiteRT-LM repository under
     `prebuilt/android_arm64/` since 2025-12.
  2. The SIGABRT (`No dispatch library found` / `No usable Dispatch runtime found`) came from our
     APK not bundling the published dispatch library. Our July LiteRT-LM version (0.14.0) runs on
     the NPU with Google's prebuilt dispatch library from the LiteRT v2.1.6 release (2026-07-02).
  3. Our manifest lacked the `<uses-native-library>` declarations for `libOpenCL.so` and
     `libvndksupport.so` that the LiteRT-LM Kotlin guide requires
     ([LiteRT-LM#1860](https://github.com/google-ai-edge/LiteRT-LM/issues/1860)). With them, ML
     Drift runs on both phones; a control build without them fails again. The July claim also
     covered the Pixel 9 (Tensor G4), which is not re-tested.
- **What remains an upstream problem.** The published SM8550 Gemma 3 1B NPU bundle gives wrong
  output on the S23 past one 128-token prefill chunk
  ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)).
- **What changed.** Finding `litert-lm-npu-blocked` was replaced by `litert-lm-npu-integration`;
  ENGINES.md, README and METHODOLOGY.md (then "GPU backend reality on stock Android", now "GPU
  backend on stock Android") were rewritten. The
  accelerator experiment ([docs/experiments/litert-accelerators.md](./experiments/litert-accelerators.md),
  PR [#29](https://github.com/a-ghorbani/xebench/pull/29)) later measured these paths with an
  output-correctness gate: F8 Ultra bundle
  `c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972`, S23 bundle
  `1890e4b41626dbad97995990e8e1eadb838baa1a278989be7a4bbb354b18f403`.
