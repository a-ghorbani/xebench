# Engine profiles — why each engine exists, where it shines, and whether we measure it there

This is the doc that gives the cross-engine comparison meaning. A tokens/sec table without this context is worse than useless — it quietly misleads, because each engine is built for a *different* job and shows its worst face when benchmarked outside its purpose.

**The rule this doc enforces:** a fair comparison runs each engine on its *intended* path. Benchmarking an accelerator-first engine (LiteRT, GENIE-X) only on CPU, then publishing the number, is the same unfairness as comparing one engine's Q4_K_M against another's Q4_0 — just moved to the backend axis. Every engine profile below ends with an honest **"Are we measuring it where it shines?"** verdict. Any ❌ is a coverage bug to fix, not a result to publish as-is.

## Summary

| Engine | Why it exists (one line) | Shines most on | Are we measuring it there? |
|---|---|---|---|
| **llama.cpp** (llama.rn) | Portable, model-universal CPU-first inference that runs *anywhere* | **CPU**, any SoC; broadest model/quant coverage | ✅ **Yes** — CPU everywhere, + its OpenCL GPU (Adreno) and own Hexagon NPU |
| **ExecuTorch** (rn-executorch) | Ship the exact PyTorch model on-device with delegate acceleration | **XNNPACK CPU** (mobile), + Qualcomm/CoreML NPU **delegates** | ⚠️ **Partial** — CPU only; its NPU delegate isn't exposed in the RN binding |
| **LiteRT-LM** (Google AI Edge) | One runtime, vendor-accelerated across the fragmented SoC landscape | **NPU** (Qualcomm/MediaTek/Tensor), then GPU | ⚠️ **Partial** — NPU and GPU run for third parties (S23, F8 Ultra spikes); SM8550 NPU bundle gives wrong output past one prefill chunk ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)); not yet measured as board rows |
| **GENIE-X** (Qualcomm) | Squeeze max perf/efficiency out of Snapdragon Hexagon NPUs | **Qualcomm Hexagon NPU** on flagship Snapdragons | ✅ **NPU measured** (F8 Ultra v81, Llama 3.2 1B W4A16: **354.6 / 16.8** — wins prefill, loses decode). CPU fallback also measured |
| *MLX* (Apple) | Array/ML framework native to Apple silicon | **Apple GPU (Metal)**, M-/A-series | ➖ Cited-only (no iOS/macOS harness yet) |
| *Core ML* (Apple) | Apple's runtime targeting the Neural Engine | **ANE** | ➖ Cited-only |
| *Apple Foundation Models* | Apple's built-in system LLM | **ANE** | ➖ Cited-only |

Verdict at a glance: **llama.cpp and GENIE-X are now measured at/near their best; LiteRT and (partly) ExecuTorch are not.** GENIE-X's NPU is measured (see below); LiteRT's NPU and GPU paths run in spikes but have no board rows yet, and ExecuTorch's NPU delegate isn't exposed in its RN binding. The board must keep saying so for the ❌/⚠️ engines.

## The headline finding: same NPU, very different integration effort (LiteRT vs GENIE-X)

Both LiteRT-LM and GENIE-X exist for the *same* thing — running LLMs on the Qualcomm Hexagon NPU. We tried both on the same class of hardware. **Both NPU stacks run for a third party.** The contrast is about **how much assembly each stack needs, and whether the output is correct**. It is not about the silicon.

> **Correction (2026-10-17).** An earlier version of this section said LiteRT-LM's Qualcomm NPU path *could not run for a third party*, because Google withholds two prebuilt bridge libraries. **That was wrong.** Both libraries are published, and our own build was the problem. See [Correction: LiteRT NPU and GPU](#correction-2026-10-17-litert-npu-and-gpu) below.

- **GENIE-X (Qualcomm's own stack) — works end-to-end.** Given a device+version-matched context binary, Genie 1.19 loaded and ran Llama 3.2 1B on the F8 Ultra's Hexagon v81: **prefill 354.6 / decode 16.8 tok/s** (n=3, engine profiler). Qualcomm ships the *complete* runtime (`genie-t2t-run`, `libGenie.so`, the full QNN stack, per-SoC skels) in the public QAIRT SDK, and the community/AI-Hub ecosystem ships matching context binaries. It just runs.
- **LiteRT-LM (Google's stack) — runs, but you assemble it by hand, and one published bundle gives wrong output.** In 2026-10-17 spikes (Gemma 3 1B, one run each, not board rows), the `litertlm-android` AAR ran on the NPU of the Galaxy S23 (SM8550, Hexagon v73) and the POCO F8 Ultra (SM8850, v81). To get there you assemble **four artifacts by hand**:
  1. the `litertlm-android` AAR;
  2. Google's NPU runtime from Google Maven, `com.google.ai.edge.litert:litert-npu-runtime-qualcomm`, which provides `libLiteRtDispatch_Qualcomm.so`;
  3. a QAIRT SDK whose version matches the `LITERT_REF` of that LiteRT-LM release;
  4. `<uses-native-library>` declarations in the app manifest (`libcdsprpc.so` for the NPU, which our manifest already had; `libOpenCL.so` and `libvndksupport.so` for the GPU).

  A mismatch fails with opaque errors. A dispatch library built for a different `LITERT_REF` logs `Unsupported dispatch runtime version`. On 0.14.0 that is a native SIGABRT the Kotlin API never sees. On 0.18.0 the line appears only in logcat, and Kotlin gets a generic `Failed to create engine`. The output problem is in Google's published bundle. The SM8550 Gemma 3 1B bundle (`Gemma3-1B-IT_q4_ekv1280_sm8550.litertlm`, built with QAIRT 2.34.0) produces degenerate output on the S23 for prompts longer than one 128-token prefill chunk ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)). The SM8850 bundle (QAIRT 2.44) passed our three-prompt output check on the F8 Ultra.

**Same NPU, same hardware: Qualcomm ships a complete stack. LiteRT's pieces are all public, but the integrator has to assemble them, a mismatch fails opaquely, and one published bundle gives wrong output.** This is a finding about integration friction and correctness. It is not a finding that Google withholds parts, and it is still the kind of "claim vs. what actually works" gap this project exists to expose.

### Correction (2026-10-17): LiteRT NPU and GPU

In July 2026 we published three claims about LiteRT-LM that a device re-evaluation on 2026-10-17 (Galaxy S23 and POCO F8 Ultra) showed to be false:

| We said | What is actually true |
|---|---|
| Google withholds the prebuilt Qualcomm bridge libs `libLiteRtDispatch_Qualcomm.so` and `libGemmaModelConstraintProvider.so`, so the NPU path is "blocked upstream". | Both are published. Per-arch dispatch libs have been attached to LiteRT GitHub releases since v2.1.0rc1 (2025-11) (`litert_npu_runtime_libraries.zip`). They are also on Google Maven as `litert-npu-runtime-qualcomm` (2.3.0, published 2026-10-06). `libGemmaModelConstraintProvider.so` has been committed in the LiteRT-LM repo at `prebuilt/android_arm64/` since 2025-12 (tags v0.11.0, v0.14.0, v0.18.0), so the CLI path was never blocked by it. The dispatch lib can also be built from source in about 8 minutes from the `LITERT_REF` that matches the LiteRT-LM release. |
| The NPU run SIGABRTed on a missing Hexagon v73 QNN skel. | The SIGABRT (`No dispatch library found` / `No usable Dispatch runtime found`) happened because our APK did not bundle the published dispatch lib. With Google's prebuilt dispatch lib from the LiteRT v2.1.6 release (2026-07-02), our July LiteRT-LM version (0.14.0) also runs on the NPU. |
| ML Drift GPU "can't find OpenCL", because an app can't load the vendor `libOpenCL.so` on stock Android. | Our manifest did not declare `<uses-native-library>` for `libOpenCL.so` and `libvndksupport.so` (`required="false"`). The LiteRT-LM Kotlin guide has required these declarations since 0.14.0 ([LiteRT-LM#1860](https://github.com/google-ai-edge/LiteRT-LM/issues/1860)). With the declarations, GPU runs on both phones. A control build without them fails again on both phones: OpenCL does not load (`libvndksupport.so` not found). On the F8 Ultra the run then ends with the same `Can not find OpenCL library on this device` error we saw in July. On the S23, ML Drift falls back to WebGPU and engine creation fails. This was our bug. |

What remains true: the S23 NPU output problem above is real, and it is upstream. CPU and GPU outputs are correct on both phones. None of these spikes is a published row, so the board still has no LiteRT NPU or GPU measurements of ours.

---

## llama.cpp (via llama.rn)

- **What / who:** ggml-based C/C++ LLM inference (ggml-org / Georgi Gerganov), GGUF format. Reaches PocketPal through `llama.rn`.
- **Why it exists:** the *universal baseline* — dependency-light, portable inference that runs on essentially any CPU, with the broadest model and quant coverage and day-one support for new architectures. If a model exists as a GGUF, llama.cpp runs it.
- **Where it shines most:** **CPU**, on any SoC — hand-tuned ARM NEON / i8mm / dotprod kernels make it the strongest CPU decode engine we measured (it beats LiteRT on Qwen decode). Secondary: an **OpenCL GPU** path (Adreno) and an **experimental Hexagon NPU** backend (`ggml-hexagon`) that runs a normal GGUF on the DSP with no vendor SDK.
- **Where it's *not* the point:** GPU/NPU are secondary and uneven — OpenCL only initializes on Adreno (not Mali/Tensor), and the Hexagon backend, while functional, *lost to the CPU* on the 8 Elite Gen 5 (Oryon cores too strong for a 1B). It is not architected around vendor accelerators.
- **Our coverage — ✅ measured at its best (and beyond):** CPU across Pixel 9 / S23 / OnePlus 6 / F8 Ultra, plus OpenCL GPU (S23, F8 Ultra) and its own Hexagon NPU (F8 Ultra, v81). This is *why the board tilts toward llama.cpp* — it's the one engine whose happy path we fully lit up.

## ExecuTorch (via react-native-executorch)

- **What / who:** PyTorch's on-device runtime (Meta). Exports a PyTorch model to `.pte` and runs it through a small runtime with **backend delegates** — XNNPACK (CPU), and vendor delegates for Qualcomm QNN, MediaTek, Core ML, and Vulkan.
- **Why it exists:** the **PyTorch author-to-device pipeline** — ship the *exact* model you trained/researched in PyTorch (including custom architectures), with delegate-based hardware acceleration. It's Meta's official Llama-on-device path.
- **Where it shines most:** on mobile, **XNNPACK CPU with SpinQuant / QAT** quantization — genuinely strong prefill (ExecuTorch wins Llama 3.2 prefill in our data). Its bigger differentiator is the **delegate NPU story** (Qualcomm/Core ML) for teams who export for it.
- **Where it's *not* the point:** the RN package we use (`react-native-executorch 0.9.2`) exposes **XNNPACK CPU only** — none of the NPU/GPU delegates are surfaced through the binding.
- **Our coverage — ⚠️ partial:** we measure a *legitimate* primary ExecuTorch path (XNNPACK CPU, strong prefill), but its accelerated delegate path — a real part of why it exists — is inaccessible in the RN package, so it's unmeasured. Honest, but incomplete.

## LiteRT-LM (Google AI Edge)

- **What / who:** Google's on-device runtime (the TensorFlow Lite successor) plus an LLM layer. Converts to `.litertlm`; runs on CPU (XNNPACK), **GPU (ML Drift)**, and **NPU** (Qualcomm QNN / MediaTek NeuroPilot / Google Tensor) through a common CompiledModel/delegate API.
- **Why it exists:** to solve the **fragmented-SoC problem** — convert once, run *vendor-accelerated everywhere*, without hand-writing kernels per chip. Its headline program is cross-vendor **NPU** acceleration.
- **Where it shines most:** **the NPU.** Google's own figures: ~1600 pp / 28 dec on a Dimensity 9500 NPU — **up to 12× CPU and 10× GPU**. This is precisely what llama.cpp can't do. GPU (ML Drift) is the secondary strength. On CPU it's table-stakes — though its int4 *prefill* is genuinely fast (F8 Ultra Gemma: 691 pp vs llama.cpp's 382).
- **Where it's *not* the point:** **CPU decode**, where it fights llama.cpp's tuned kernels and loses.
- **Our coverage — ❌ still measuring the wrong thing (fixable from our side):** every measured LiteRT row is **CPU-only**. Its **NPU** path (its entire reason to exist) has **0 rows**. Our July runs aborted with `No dispatch library found` / `No usable Dispatch runtime found` because **our APK did not bundle Google's published dispatch lib** (`libLiteRtDispatch_Qualcomm.so`). At the time we blamed a missing Hexagon v73 skel, which was wrong. Its **GPU** path (ML Drift) also has **0 rows**. It failed to find OpenCL because **our manifest lacked the `<uses-native-library>` declarations** for `libOpenCL.so` / `libvndksupport.so` that the LiteRT-LM Kotlin guide requires ([LiteRT-LM#1860](https://github.com/google-ai-edge/LiteRT-LM/issues/1860)). With both fixed, 2026-10-17 spikes ran NPU and GPU on the S23 and F8 Ultra. However, the published SM8550 NPU bundle gives wrong output on the S23 past one 128-token prefill chunk ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)), so NPU rows must pass an output-correctness check before they are published. **Until those rows land, we are still benchmarking LiteRT on the one path it is not built for.** This is the single biggest hole in the comparison.

## GENIE-X (Qualcomm)

- **What / who:** Qualcomm's on-device GenAI SDK (Genie / GENIE-X). Runs LLMs on Snapdragon via QAIRT/QNN on the **Hexagon NPU**, with a `llama.cpp` CPU plugin as a compatibility fallback.
- **Why it exists:** to extract **maximum performance and power-efficiency from Qualcomm Snapdragon NPUs** — first-party Hexagon acceleration tuned to the SoC's HTP units and power envelope.
- **Where it shines most:** the **Qualcomm Hexagon NPU** on flagship Snapdragons (8 Gen 2/3, 8 Elite). That is the whole product.
- **Where it's *not* the point:** its CPU (`llama_cpp`) plugin is a fallback/compat path, not the reason anyone picks GENIE-X; non-Qualcomm silicon is out of scope entirely.
- **Our coverage — ✅ NPU measured (2026-07-21):** using Qualcomm's own `genie-t2t-run` from the QAIRT 2.48.40 SDK + a version-matched context binary (runanywhere's `llama3.2-1b-...-8elite-gen5`, built with QAIRT 2.42.0), Genie 1.19 ran Llama 3.2 1B on the **F8 Ultra's Hexagon v81**: **prefill 354.6 / decode 16.8 tok/s** (n=3, engine profiler). Finding: **wins prefill outright (beats llama.cpp CPU/HTP and ExecuTorch), loses decode badly** (16.8 vs 34–47) — the HMX units crush compute-heavy prefill, but memory-bound decode doesn't benefit and the Oryon CPU is hard to beat; W4A16 (16-bit activations) deepens the decode gap. Caveats on the row: CLI-measured (adb-shell) so decode's per-token CPU orchestration may be slightly understated (prefill is DSP-bound, representative); W4A16 quant differs from other rows. The earlier CPU-plugin row (S23, 87.9/12.3) remains as the fallback data point.

## Apple-silicon engines (MLX, Core ML, Apple Foundation Models)

- **MLX** — Apple's array/ML framework; shines on the **Apple GPU (Metal)** on M-series/A-series. **Core ML** — Apple's runtime; shines on the **ANE (Apple Neural Engine)**. **Apple Foundation Models** — Apple's built-in system LLM (ANE).
- **Our coverage — ➖ cited-only:** iOS/macOS-only, and we have no iOS harness, so these appear on the board *only* as vendor/community-cited numbers, never as our measurements. Honest, but it means the entire Apple-accelerator story is unverified by us.

---

## Reading the comparison honestly

1. **Only llama.cpp is measured at its best.** LiteRT and GENIE-X are shown on their CPU *fallback*, not their NPU *purpose*; ExecuTorch is CPU-only because its delegates aren't in the RN binding; the Apple engines are cited-only. So the board, as-is, **systematically understates the accelerator-first engines**.
2. **Therefore the board must say so.** Until the gaps close, LiteRT / GENIE-X / ExecuTorch cards should carry an explicit note: *"shown CPU-only; its NPU/accelerated path — the reason it exists — is unmeasured here."* Implying a fair fight we haven't run is the thing to avoid.
3. **A cross-engine winner claim is only valid within the same backend class.** "llama.cpp beats LiteRT" is true *on CPU*; it says nothing about LiteRT-on-NPU, which is a different, unmeasured contest.

## Coverage-gap roadmap (what to light up so each engine gets a fair shot)

| Priority | Gap | Status / concrete unblock |
|---|---|---|
| **✅ done** | GENIE-X **NPU** | **Measured 2026-07-21** (F8 Ultra v81, Llama 3.2 1B W4A16: 354.6 / 16.8). Path: Qualcomm `genie-t2t-run` (QAIRT 2.48.40 SDK) + a version-matched context binary (runanywhere `8elite-gen5` bundle, QAIRT 2.42.0). Key: the model binary must match *both* the device's Hexagon arch (v81) *and* a QAIRT version you can actually get — the easy ones are the "Community" track (2.42/2.48, direct download); older mainline versions (2.28) need the Linux-x86-only QPM. |
| **1** | Measure LiteRT **NPU / GPU**, gated on output correctness | **Unblocked; our side to do.** Both paths ran in 2026-10-17 spikes on the S23 and F8 Ultra (Gemma 3 1B, one run each, not board rows). The working stack is `litertlm-android` 0.18.0 + `com.google.ai.edge.litert:litert-npu-runtime-qualcomm:2.3.0` (Google Maven) + QAIRT 2.50.0.260828 (public Community download) + `<uses-native-library>` manifest declarations (`libcdsprpc.so` for NPU; `libOpenCL.so` and `libvndksupport.so`, `required="false"`, for GPU). The QAIRT version and the dispatch lib must match the release's `LITERT_REF`. A mismatched dispatch lib logs `Unsupported dispatch runtime version`: a native SIGABRT on 0.14.0, and a generic Kotlin `Failed to create engine` on 0.18.0. This repo's harness still pins 0.14.0 and does not yet declare the GPU libs, so it needs updating first. **Next:** wire this into the harness, add an output-correctness check with a prompt that spans more than one 128-token prefill chunk, and publish only rows that pass. The S23 NPU cell stays off the board until the SM8550 bundle's multi-chunk prefill bug ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)) is fixed or a correct bundle is published. |
| **2** | LiteRT NPU on **MediaTek** | **Untested.** Google publishes the runtime (`litert-npu-runtime-mediatek:2.3.0`), but LLM bundles exist only for MT6989 / MT6991 / MT6993. Our **klee** (POCO X8 Pro) is **MT6899**, so it has no bundle. (GENIE-X is Qualcomm-only, N/A on MediaTek.) |
| **3** | ExecuTorch **delegate** (NPU) | Needs a delegate exposed beyond XNNPACK — not available in `react-native-executorch 0.9.2`; would require a native spike. |
| **4** | Genie **Rule-0-clean** decode number | Foreground path BUILT (`GenieCliBenchModule` spawns `genie-t2t-run`; app confirmed in `/top-app`) but **HyperOS blocks app `exec` of the packaged binary** — `EACCES`, `dontaudit`'d SELinux, not our bug. Next: an in-process **libGenie/JNI** wrapper (`System.loadLibrary` *is* allowed) — no exec, works on any device. Low urgency: decode is dead-stable across runs (16.72/16.90/16.78) → NPU-bound, so the CLI number is already representative. |
| **5** | Apple engines (MLX / Core ML / Foundation Models) | Build an iOS/macOS harness so these are *measured*, not just cited. |

GENIE-X now has a real NPU number. LiteRT's NPU and GPU paths run but are not yet measured as board rows, and one published NPU bundle gives wrong output. Qualcomm ships a complete NPU stack that runs as-is. Google publishes the pieces but leaves the integrator to assemble and version-match them, with opaque errors on a mismatch. That contrast in integration friction and correctness is the board's headline finding. (Our earlier "blocked upstream" framing was wrong; see the correction above.)
