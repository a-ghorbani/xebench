# Engine profiles — why each engine exists, where it shines, and whether we measure it there

This is the doc that gives the cross-engine comparison meaning. A tokens/sec table without this context is worse than useless — it quietly misleads, because each engine is built for a *different* job and shows its worst face when benchmarked outside its purpose.

**The rule this doc enforces:** a fair comparison runs each engine on its *intended* path. Benchmarking an accelerator-first engine (LiteRT, GENIE-X) only on CPU, then publishing the number, is the same unfairness as comparing one engine's Q4_K_M against another's Q4_0 — just moved to the backend axis. Every engine profile below ends with an honest **"Are we measuring it where it shines?"** verdict. Any ❌ is a coverage bug to fix, not a result to publish as-is.

## Summary

| Engine | Why it exists (one line) | Shines most on | Are we measuring it there? |
|---|---|---|---|
| **llama.cpp** (llama.rn) | Portable, model-universal CPU-first inference that runs *anywhere* | **CPU**, any SoC; broadest model/quant coverage | ✅ **Yes** — CPU everywhere, + its OpenCL GPU (Adreno). Hexagon NPU on the F8 Ultra: wrong output with llama.rn 0.12.4, correct output for Llama 3.2 1B Q4_K_M and Gemma 3 1B with 0.13.0-rc.7; board row: Llama 3.2 1B Q4_K_M |
| **ExecuTorch** (rn-executorch) | Ship the exact PyTorch model on-device with delegate acceleration | **XNNPACK CPU** (mobile), + Qualcomm/CoreML NPU **delegates** | ⚠️ **Partial** — CPU only; its NPU delegate isn't exposed in the RN binding |
| **LiteRT-LM** (Google AI Edge) | One runtime, vendor-accelerated across the fragmented SoC landscape | **NPU** (Qualcomm/MediaTek/Tensor), then GPU | ⚠️ **Partial** — the board has its NPU on the POCO F8 Ultra (Qualcomm SM8850) and its CPU; its GPU is measured only in an [experiment](./docs/experiments/litert-accelerators.md), and the SM8550 NPU bundle gives wrong output past one prefill chunk ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)) |
| **GENIE-X** (Qualcomm) | Squeeze max perf/efficiency out of Snapdragon Hexagon NPUs | **Qualcomm Hexagon NPU** on flagship Snapdragons | ✅ **NPU measured** (F8 Ultra v81, Llama 3.2 1B W4A16: **354.6 / 16.8**; decode below the F8 Ultra's CPU rows). CPU fallback also measured |
| *MLX* (Apple) | Array/ML framework native to Apple silicon | **Apple GPU (Metal)**, M-/A-series | ➖ Cited-only (no iOS/macOS harness yet) |
| *Core ML* (Apple) | Apple's runtime targeting the Neural Engine | **ANE** | ➖ Cited-only |
| *Apple Foundation Models* | Apple's built-in system LLM | **ANE** | ➖ Cited-only |

Verdict at a glance: **llama.cpp and GENIE-X are measured at/near their best; LiteRT-LM is on one Qualcomm NPU, and ExecuTorch is not.** GENIE-X's NPU is measured (see below); LiteRT-LM's NPU has a board row on the POCO F8 Ultra, its GPU only an experiment, and ExecuTorch's NPU delegate isn't exposed in its RN binding. The board must keep saying so for the ❌/⚠️ engines.

## The headline finding: same NPU, very different integration effort (LiteRT vs GENIE-X)

*Corrected 2026-10-17; see [docs/CORRECTIONS.md](./docs/CORRECTIONS.md).*

Both LiteRT-LM and GENIE-X exist for the *same* thing — running LLMs on the Qualcomm Hexagon NPU. Both NPU stacks run for a third party on the same class of hardware. The contrast is **how much assembly each stack needs, and whether the output is correct**. It is not about the silicon.

- **GENIE-X (Qualcomm's own stack) — works end-to-end.** Given a device+version-matched context binary, Genie 1.19 loaded and ran Llama 3.2 1B on the F8 Ultra's Hexagon v81: **prefill 354.6 / decode 16.8 tok/s** (n=3, engine profiler). Qualcomm ships the *complete* runtime (`genie-t2t-run`, `libGenie.so`, the full QNN stack, per-SoC skels) in the public QAIRT SDK, and the community/AI-Hub ecosystem ships matching context binaries. It just runs.
- **LiteRT-LM (Google's stack) — runs once you assemble four artifacts, and one published bundle gives wrong output.** With `litertlm-android` 0.18.0, Gemma 3 1B runs on the NPU of the POCO F8 Ultra (SM8850, Hexagon v81) and the Galaxy S23 (SM8550, v73) ([accelerator experiment](./docs/experiments/litert-accelerators.md)). It needs:
  1. the `litertlm-android` AAR;
  2. Google's NPU runtime from Google Maven, `com.google.ai.edge.litert:litert-npu-runtime-qualcomm`, which provides `libLiteRtDispatch_Qualcomm.so`;
  3. a QAIRT SDK whose version matches the `LITERT_REF` of that LiteRT-LM release;
  4. `<uses-native-library>` declarations in the app manifest (`libcdsprpc.so` for the NPU; `libOpenCL.so` and `libvndksupport.so` for the GPU).

  A mismatch fails with opaque errors: a dispatch library built for a different `LITERT_REF` logs `Unsupported dispatch runtime version`, and on 0.18.0 the Kotlin API reports only a generic `Failed to create engine`. The output problem is in Google's published bundle: the SM8550 Gemma 3 1B bundle (`Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm`, built with QAIRT 2.34.0) loses prompt content after the first 128-token prefill chunk on the S23, so probes whose fact comes after that chunk get wrong answers ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)). The SM8850 bundle (QAIRT 2.44) passes the output-correctness gate on the F8 Ultra.

**Same NPU, same hardware: Qualcomm ships a complete stack. LiteRT's pieces are all public, but the integrator has to assemble them, a mismatch fails opaquely, and one published bundle gives wrong output.** This is a finding about integration friction and correctness — the kind of "claim vs. what actually works" gap this project exists to expose.

---

## llama.cpp (via llama.rn)

- **What / who:** ggml-based C/C++ LLM inference (ggml-org / Georgi Gerganov), GGUF format. Reaches PocketPal through `llama.rn`.
- **Why it exists:** the *universal baseline* — dependency-light, portable inference that runs on essentially any CPU, with the broadest model and quant coverage and day-one support for new architectures. If a model exists as a GGUF, llama.cpp runs it.
- **Where it shines most:** **CPU**, on any SoC — hand-tuned ARM NEON / i8mm / dotprod kernels make it the strongest CPU decode engine we measured (it beats LiteRT on Qwen decode). Secondary: an **OpenCL GPU** path (Adreno) and an **experimental Hexagon NPU** backend (`ggml-hexagon`) that runs a normal GGUF on the DSP with no vendor SDK (output on the F8 Ultra: wrong with llama.rn 0.12.4, correct for Llama 3.2 1B Q4_K_M and Gemma 3 1B with 0.13.0-rc.7; see below).
- **Where it's *not* the point:** GPU/NPU are secondary and uneven — OpenCL only initializes on Adreno (not Mali/Tensor). It is not architected around vendor accelerators.
- **Hexagon output by llama.rn version** (POCO F8 Ultra, `HTP0`, gated against llama.cpp on CPU with the same model file). *Corrected 2026-10-17; see [docs/CORRECTIONS.md](./docs/CORRECTIONS.md).*
  - **llama.rn 0.12.4 — wrong output.** Llama 3.2 1B (Q4_K_M and Q4_0) answers a ~20-token prompt correctly but degenerates into repeated fragments on every tested prompt over ~128 tokens. Gemma 3 1B (QAT Q4_0) fails even the short prompt. On CPU, the same Q4_K_M and Gemma files pass every answer probe. Evidence: [Llama 3.2](./evidence/7cd4368bf08f339b78e731c2eed789768de44f736f338ad38f06ee154c3ae478/), [Gemma 3](./evidence/c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972/).
  - **llama.rn 0.13.0-rc.7 — correct output.** Llama 3.2 1B Q4_K_M and Gemma 3 1B pass every answer probe; Llama 3.2 1B Q4_0 passes the same probes as its CPU reference, which misses one. Evidence: [Llama 3.2](./evidence/20009fec0a56fe4bcb2b0046a2e160e3fca5c9263de1fef8a364dc62674ca210/), [Gemma 3](./evidence/beb9a67ced8709bf195a64d048be0d8529505f5b76e24ee612de2d8867046bd2/).
  - **Board.** The board's llama.cpp NPU row is Llama 3.2 1B Q4_K_M on the F8 Ultra, measured with llama.rn 0.13.0-rc.7 and gated against its CPU reference. The July 2026 Q4_0 row (llama.rn 0.12.4, no output check) is not on it; its history is in [docs/CORRECTIONS.md](./docs/CORRECTIONS.md). Details: ["Legacy Hexagon row check"](https://github.com/a-ghorbani/xebench/blob/ea2f89d592bb4f01a51da595abf867b84e413321/docs/experiments/litert-accelerators.md#legacy-hexagon-row-check-poco-f8-ultra-llama-32-1b-2026-10-17) and ["llama.rn 0.13.0-rc.7 re-check"](https://github.com/a-ghorbani/xebench/blob/ea2f89d592bb4f01a51da595abf867b84e413321/docs/experiments/litert-accelerators.md#llamarn-0130-rc7-re-check-poco-f8-ultra-2026-10-18).
- **Our coverage — ✅ measured at its best:** with llama.rn 0.13.0-rc.7, CPU on the Pixel 9, S23 and F8 Ultra, OpenCL GPU on the F8 Ultra and the Hexagon NPU on the F8 Ultra (Llama 3.2 1B Q4_K_M, the pinned reference model, with correct output; see above). Three setups have no board row: the OnePlus 6 throttles too hard to complete the run conditions, on the S23 the OpenCL run is killed for low memory, and on the S23 the Qwen2.5 1.5B Q8_0 CPU prefill falls across repetitions within a session. This is *why the board tilts toward llama.cpp*: CPU is its happy path, and we fully lit it up.

## ExecuTorch (via react-native-executorch)

- **What / who:** PyTorch's on-device runtime (Meta). Exports a PyTorch model to `.pte` and runs it through a small runtime with **backend delegates** — XNNPACK (CPU), and vendor delegates for Qualcomm QNN, MediaTek, Core ML, and Vulkan.
- **Why it exists:** the **PyTorch author-to-device pipeline** — ship the *exact* model you trained/researched in PyTorch (including custom architectures), with delegate-based hardware acceleration. It's Meta's official Llama-on-device path.
- **Where it shines most:** on mobile, **XNNPACK CPU with SpinQuant / QAT** quantization — genuinely strong prefill (ExecuTorch wins Llama 3.2 prefill in our data). Its bigger differentiator is the **delegate NPU story** (Qualcomm/Core ML) for teams who export for it.
- **Where it's *not* the point:** the RN package we use (`react-native-executorch 0.9.2`) exposes **XNNPACK CPU only** — none of the NPU/GPU delegates are surfaced through the binding.
- **Our coverage — ⚠️ partial:** we measure a *legitimate* primary ExecuTorch path (XNNPACK CPU, strong prefill), but its accelerated delegate path — a real part of why it exists — is inaccessible in the RN package, so it's unmeasured. Honest, but incomplete.

## LiteRT-LM (Google AI Edge)

- **What / who:** Google's on-device runtime (the TensorFlow Lite successor) plus an LLM layer. Converts to `.litertlm`; runs on CPU (XNNPACK), **GPU (ML Drift)**, and **NPU** (Qualcomm QNN / MediaTek NeuroPilot / Google Tensor) through a common CompiledModel/delegate API.
- **Why it exists:** to solve the **fragmented-SoC problem** — convert once, run *vendor-accelerated everywhere*, without hand-writing kernels per chip. Its headline program is cross-vendor **NPU** acceleration.
- **Where it shines most:** **the NPU.** Google's own figures: ~1600 pp / 28 dec on a Dimensity 9500 NPU — **up to 12× CPU and 10× GPU**. llama.rn 0.13.0-rc.7 ships one NPU backend for llama.cpp, Qualcomm Hexagon (`ggml-hexagon`); MediaTek and Tensor NPUs have none. GPU (ML Drift) is the secondary strength. On CPU it's table-stakes — though its int4 *prefill* is fast on the F8 Ultra (Gemma 3 1B: 691.2 tok/s against llama.cpp's 370.4; on the Galaxy S23 llama.cpp prefills faster, 251.3 against 228.1).
- **Where it's *not* the point:** **CPU decode**, where it meets llama.cpp's tuned kernels: llama.cpp decodes Qwen2.5 1.5B faster on the F8 Ultra (33.2 against 25.5 tok/s) and the Pixel 9 (14.4 against 12.2), and Gemma 3 1B about as fast on the F8 Ultra (48.8 against 47.4); on the Galaxy S23 LiteRT-LM decodes Gemma 3 1B faster (38.6 against 22.8).
- **Our coverage — ⚠️ one NPU, no GPU:** the board's LiteRT-LM rows are measured with LiteRT-LM 0.18.0: Gemma 3 1B on the POCO F8 Ultra's NPU (Google's SM8850 bundle) and Gemma 3 1B and Qwen2.5 1.5B on CPU (F8 Ultra, Galaxy S23, Pixel 9). Every row passes the output-correctness gate and decodes 128 tokens. The NPU row's backend is confirmed from the app's own log: for each measured load, the Qualcomm dispatch library and HTP backend in use and a QNN context created from the bundle ([rule](./docs/RUN-CONDITIONS.md#litert-lm-npu-backend-confirmed-by-the-apps-log-litert-npu-log-v1)). The S23 has no NPU row: its SM8550 bundle gives wrong output ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)). The GPU path is measured only in the [accelerator experiment](./docs/experiments/litert-accelerators.md): nothing in the record confirms that the GPU executes. It needs the `<uses-native-library>` declarations for `libOpenCL.so` / `libvndksupport.so` that the LiteRT-LM Kotlin guide requires ([LiteRT-LM#1860](https://github.com/google-ai-edge/LiteRT-LM/issues/1860)); the NPU path needs Google's dispatch lib (`libLiteRtDispatch_Qualcomm.so`, from `litert-npu-runtime-qualcomm`). MediaTek and Tensor NPUs are not on the board.

## GENIE-X (Qualcomm)

- **What / who:** Qualcomm's on-device GenAI SDK (Genie / GENIE-X). Runs LLMs on Snapdragon via QAIRT/QNN on the **Hexagon NPU**, with a `llama.cpp` CPU plugin as a compatibility fallback.
- **Why it exists:** to extract **maximum performance and power-efficiency from Qualcomm Snapdragon NPUs** — first-party Hexagon acceleration tuned to the SoC's HTP units and power envelope.
- **Where it shines most:** the **Qualcomm Hexagon NPU** on flagship Snapdragons (8 Gen 2/3, 8 Elite). That is the whole product.
- **Where it's *not* the point:** its CPU (`llama_cpp`) plugin is a fallback/compat path, not the reason anyone picks GENIE-X; non-Qualcomm silicon is out of scope entirely.
- **Our coverage — ✅ NPU measured (2026-07-21):** using Qualcomm's own `genie-t2t-run` from the QAIRT 2.48.40 SDK + a version-matched context binary (runanywhere's `llama3.2-1b-...-8elite-gen5`, built with QAIRT 2.42.0), Genie 1.19 ran Llama 3.2 1B on the **F8 Ultra's Hexagon v81**: **prefill 354.6 / decode 16.8 tok/s** (n=3, engine profiler). Finding: **decode below the F8 Ultra's CPU rows** (16.8 vs 34.3 to 51.8 tok/s for its ExecuTorch and llama.cpp Llama 3.2 1B CPU rows, other quants); the row states no prompt, so its prefill is not compared. On the same NPU, llama.cpp's Hexagon backend (llama.rn 0.13.0-rc.7, Llama 3.2 1B Q4_K_M) decodes at 57.0 tok/s against 47.3 on the CPU, so slow decode is not inherent to the F8 Ultra's NPU; Genie's W4A16 model uses 16-bit activations. Caveats on the row: CLI-measured (adb-shell) so decode's per-token CPU orchestration may be slightly understated (prefill is DSP-bound, representative); W4A16 quant differs from other rows. The earlier CPU-plugin row (S23, 87.9/12.3) remains as the fallback data point.

## Apple-silicon engines (MLX, Core ML, Apple Foundation Models)

- **MLX** — Apple's array/ML framework; shines on the **Apple GPU (Metal)** on M-series/A-series. **Core ML** — Apple's runtime; shines on the **ANE (Apple Neural Engine)**. **Apple Foundation Models** — Apple's built-in system LLM (ANE).
- **Our coverage — ➖ cited-only:** iOS/macOS-only, and we have no iOS harness, so these appear on the board *only* as vendor/community-cited numbers, never as our measurements. Honest, but it means the entire Apple-accelerator story is unverified by us.

---

## Reading the comparison honestly

1. **Only llama.cpp is measured at its best on the board.** LiteRT-LM has one NPU row (Gemma 3 1B on the POCO F8 Ultra) next to its CPU rows, and its GPU is measured only in the [accelerator experiment](./docs/experiments/litert-accelerators.md); GENIE-X's NPU has one CLI-measured row; ExecuTorch is CPU-only because its delegates aren't in the RN binding; the Apple engines are cited-only. So the board, as-is, **systematically understates the accelerator-first engines**.
2. **Therefore the board must say so.** Where a card shows an engine only on CPU, it should carry an explicit note: *"shown CPU-only; its NPU/accelerated path — the reason it exists — is unmeasured here."* Implying a fair fight we haven't run is the thing to avoid.
3. **A cross-engine winner claim is only valid within the same backend class and phone.** "llama.cpp beats LiteRT-LM on Qwen2.5 decode" holds *on CPU* on the F8 Ultra; it says nothing about LiteRT-LM on the NPU, a different contest.

## Coverage-gap roadmap (what to light up so each engine gets a fair shot)

| Priority | Gap | Status / concrete unblock |
|---|---|---|
| **✅ done** | GENIE-X **NPU** | **Measured 2026-07-21** (F8 Ultra v81, Llama 3.2 1B W4A16: 354.6 / 16.8). Path: Qualcomm `genie-t2t-run` (QAIRT 2.48.40 SDK) + a version-matched context binary (runanywhere `8elite-gen5` bundle, QAIRT 2.42.0). Key: the model binary must match *both* the device's Hexagon arch (v81) *and* a QAIRT version you can actually get — the easy ones are the "Community" track (2.42/2.48, direct download); older mainline versions (2.28) need the Linux-x86-only QPM. |
| **1** | LiteRT **NPU / GPU** rows, gated on output correctness | **NPU on the board for the F8 Ultra (SM8850); GPU and the S23 NPU open.** Stack: `litertlm-android` 0.18.0 + `com.google.ai.edge.litert:litert-npu-runtime-qualcomm:2.3.0` (Google Maven) + QAIRT 2.50.0.260828 (public Community download) + `<uses-native-library>` manifest declarations (`libcdsprpc.so` for NPU; `libOpenCL.so` and `libvndksupport.so`, `required="false"`, for GPU). The QAIRT version and the dispatch lib must match the release's `LITERT_REF`; a mismatched dispatch lib logs `Unsupported dispatch runtime version` and engine creation fails (a generic Kotlin `Failed to create engine` on 0.18.0). The `experimental` flavor pins this stack, and the harness's `accelerator-v1` protocol gates output correctness with prompts that span several 128-token prefill chunks. F8 Ultra CPU/GPU/NPU and S23 CPU/GPU pass the gate ([results](https://github.com/a-ghorbani/xebench/blob/ea2f89d592bb4f01a51da595abf867b84e413321/docs/experiments/litert-accelerators.md#results)). The F8 Ultra NPU row's backend is confirmed from the app's log. **Open:** a GPU row needs evidence that the GPU executes. The S23 NPU cell stays off the board until the SM8550 bundle's multi-chunk prefill bug ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)) is fixed or a correct bundle is published. |
| **2** | LiteRT NPU on **MediaTek** | **Untested.** Google publishes the runtime (`litert-npu-runtime-mediatek:2.3.0`), but LLM bundles exist only for MT6989 / MT6991 / MT6993. Our **klee** (POCO X8 Pro) is **MT6899**, so it has no bundle. (GENIE-X is Qualcomm-only, N/A on MediaTek.) |
| **3** | ExecuTorch **delegate** (NPU) | Needs a delegate exposed beyond XNNPACK — not available in `react-native-executorch 0.9.2`; would require a native spike. |
| **4** | Genie **Rule-0-clean** decode number | Foreground path BUILT (`GenieCliBenchModule` spawns `genie-t2t-run`; app confirmed in `/top-app`) but **HyperOS blocks app `exec` of the packaged binary** — `EACCES`, `dontaudit`'d SELinux, not our bug. Next: an in-process **libGenie/JNI** wrapper (`System.loadLibrary` *is* allowed) — no exec, works on any device. Low urgency: decode is dead-stable across runs (16.72/16.90/16.78) → NPU-bound, so the CLI number is already representative. |
| **5** | Apple engines (MLX / Core ML / Foundation Models) | Build an iOS/macOS harness so these are *measured*, not just cited. |

GENIE-X has a real NPU number, and so does LiteRT-LM on the POCO F8 Ultra; one published LiteRT-LM NPU bundle gives wrong output. Qualcomm ships a complete NPU stack that runs as-is. Google publishes the pieces but leaves the integrator to assemble and version-match them, with opaque errors on a mismatch. That contrast in integration friction and correctness is the board's headline finding.
