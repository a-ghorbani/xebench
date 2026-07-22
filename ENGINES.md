# Engine profiles — why each engine exists, where it shines, and whether we measure it there

This is the doc that gives the cross-engine comparison meaning. A tokens/sec table without this context is worse than useless — it quietly misleads, because each engine is built for a *different* job and shows its worst face when benchmarked outside its purpose.

**The rule this doc enforces:** a fair comparison runs each engine on its *intended* path. Benchmarking an accelerator-first engine (LiteRT, GENIE-X) only on CPU, then publishing the number, is the same unfairness as comparing one engine's Q4_K_M against another's Q4_0 — just moved to the backend axis. Every engine profile below ends with an honest **"Are we measuring it where it shines?"** verdict. Any ❌ is a coverage bug to fix, not a result to publish as-is.

## Summary

| Engine | Why it exists (one line) | Shines most on | Are we measuring it there? |
|---|---|---|---|
| **llama.cpp** (llama.rn) | Portable, model-universal CPU-first inference that runs *anywhere* | **CPU**, any SoC; broadest model/quant coverage | ✅ **Yes** — CPU everywhere, + its OpenCL GPU (Adreno) and own Hexagon NPU |
| **ExecuTorch** (rn-executorch) | Ship the exact PyTorch model on-device with delegate acceleration | **XNNPACK CPU** (mobile), + Qualcomm/CoreML NPU **delegates** | ⚠️ **Partial** — CPU only; its NPU delegate isn't exposed in the RN binding |
| **LiteRT-LM** (Google AI Edge) | One runtime, vendor-accelerated across the fragmented SoC landscape | **NPU** (Qualcomm/MediaTek/Tensor), then GPU | ❌ **No — blocked upstream.** Google ships the runtime but withholds the prebuilt Qualcomm bridge libs ([#6889](https://github.com/google-ai-edge/LiteRT/issues/6889)); CPU-only here |
| **GENIE-X** (Qualcomm) | Squeeze max perf/efficiency out of Snapdragon Hexagon NPUs | **Qualcomm Hexagon NPU** on flagship Snapdragons | ✅ **NPU measured** (F8 Ultra v81, Llama 3.2 1B W4A16: **354.6 / 16.8** — wins prefill, loses decode). CPU fallback also measured |
| *MLX* (Apple) | Array/ML framework native to Apple silicon | **Apple GPU (Metal)**, M-/A-series | ➖ Cited-only (no iOS/macOS harness yet) |
| *Core ML* (Apple) | Apple's runtime targeting the Neural Engine | **ANE** | ➖ Cited-only |
| *Apple Foundation Models* | Apple's built-in system LLM | **ANE** | ➖ Cited-only |

Verdict at a glance: **llama.cpp and GENIE-X are now measured at/near their best; LiteRT and (partly) ExecuTorch are not.** GENIE-X's NPU is measured (see below); LiteRT's NPU is blocked by an upstream distribution gap, and ExecuTorch's NPU delegate isn't exposed in its RN binding. The board must keep saying so for the ❌/⚠️ engines.

## The headline finding: same NPU, opposite outcomes (LiteRT vs GENIE-X)

Both LiteRT-LM and GENIE-X exist for the *same* thing — running LLMs on the Qualcomm Hexagon NPU. We tried both on the same class of hardware. The result is a clean, sourced contrast about **vendor distribution completeness**, not about the silicon:

- **GENIE-X (Qualcomm's own stack) — works end-to-end.** Given a device+version-matched context binary, Genie 1.19 loaded and ran Llama 3.2 1B on the F8 Ultra's Hexagon v81: **prefill 354.6 / decode 16.8 tok/s** (n=3, engine profiler). Qualcomm ships the *complete* runtime (`genie-t2t-run`, `libGenie.so`, the full QNN stack, per-SoC skels) in the public QAIRT SDK, and the community/AI-Hub ecosystem ships matching context binaries. It just runs.
- **LiteRT-LM (Google's stack) — cannot run for a third party.** We built the entire stack; it fails on **two unpublished Google bridge libs** (`libLiteRtDispatch_Qualcomm.so`, `libGemmaModelConstraintProvider.so`) that Google builds from internal sources and does not ship prebuilt ([#6889](https://github.com/google-ai-edge/LiteRT/issues/6889)). The Qualcomm hardware libs are available; the Google glue is not.

**Same NPU, same hardware — one vendor shipped everything, the other didn't.** That is the single most important thing this comparison surfaced, and it's the kind of "claim vs. what actually ships" gap the whole project exists to expose.

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
- **Our coverage — ❌ measuring the wrong thing:** every measured LiteRT row is **CPU-only**. Its **NPU** path (its entire reason to exist) has **0 rows** — the precompiled `sm8550` bundle SIGABRTs on the missing Hexagon **v73 QNN skel**. Its **GPU** path (ML Drift) also produced **0 rows** — failed to find OpenCL in our runs, even on the Adreno S23 where llama.cpp's OpenCL worked. **We are benchmarking LiteRT doing the one thing it is not for.** This is the single biggest hole in the comparison.

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
| **1** | LiteRT **NPU** (its purpose) — **blocked upstream** | We proved the full stack works with QAIRT 2.48.40 (loads, finds the v73 skel, parses the model), but it aborts on **two unpublished Google bridge libs**: `libLiteRtDispatch_Qualcomm.so` (AAR path, [#6889](https://github.com/google-ai-edge/LiteRT/issues/6889)) and `libGemmaModelConstraintProvider.so` (CLI path — won't even link). Both are Google (LiteRT) artifacts built from internal sources; no QAIRT download can supply them. **Only escape:** bazel-build them from LiteRT source (hours; #6889 warns the public-repo ABI differs from the AAR — uncertain) or wait for Google to publish. Not our misconfiguration — an upstream distribution gap. |
| **2** | LiteRT / GENIE-X NPU on **MediaTek** | Unblock **klee** (POCO X8 Pro, Dimensity) — MediaTek is now a first-class LiteRT NPU target (NeuroPilot) — + a NeuroPilot bundle. (GENIE-X is Qualcomm-only, N/A on MediaTek.) |
| **3** | ExecuTorch **delegate** (NPU) | Needs a delegate exposed beyond XNNPACK — not available in `react-native-executorch 0.9.2`; would require a native spike. |
| **4** | Genie **Rule-0-clean** decode number | Foreground path BUILT (`GenieCliBenchModule` spawns `genie-t2t-run`; app confirmed in `/top-app`) but **HyperOS blocks app `exec` of the packaged binary** — `EACCES`, `dontaudit`'d SELinux, not our bug. Next: an in-process **libGenie/JNI** wrapper (`System.loadLibrary` *is* allowed) — no exec, works on any device. Low urgency: decode is dead-stable across runs (16.72/16.90/16.78) → NPU-bound, so the CLI number is already representative. |
| **5** | Apple engines (MLX / Core ML / Foundation Models) | Build an iOS/macOS harness so these are *measured*, not just cited. |

GENIE-X now has a real NPU number; LiteRT's is blocked by an upstream distribution gap it can't clear from our side. That contrast — one vendor ships a complete NPU stack, the other doesn't — is now the board's headline finding, not a footnote.
