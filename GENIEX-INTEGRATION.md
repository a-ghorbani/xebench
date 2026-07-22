# GENIE-X integration — status & wiring guide

**Status (2026-07-18): scaffolded, NOT built/tested. Blocked on device.**

GENIE-X is Qualcomm's on-device runtime. It's the 4th engine on the roadmap, but
it could not be tested tonight for a hard reason:

## Why it's blocked

- **Android support is Snapdragon 8 Elite / 8 Elite Gen 5 only** (per Qualcomm's
  README: "GenieX runs only on Qualcomm Snapdragon"). Of the 6 lab devices, the
  **only** supported one is the POCO F8 Ultra (myron, SM8850 = 8 Elite Gen 5).
  The S23 (SM8550 / 8 Gen 2) and all others are unsupported.
- **myron is install-blocked** — HyperOS refuses adb installs until *Settings →
  Developer options → "Install via USB"* is enabled (user-gated; user asleep).
- GENIE-X's **CPU/GPU path uses the `llama_cpp` plugin** — i.e. it *is* llama.cpp
  under the hood, redundant with our existing llama.cpp rows. Its only distinct
  value is the **`qairt` (Hexagon NPU) path**, which additionally needs a
  **precompiled QAIRT bundle** from Qualcomm AI Hub (not a GGUF).

So even with myron unblocked, a *meaningful* GENIE-X row = the QAIRT/NPU path on a
QAIRT bundle. That is the Phase-C NPU work.

## What's implemented

- `xebenchapp/src/harness/adapters/GenieXAdapter.ts` — the TS adapter, ready to
  wire (mirrors LiteRtAdapter; calls a `GenieXBench` native module).
- `EngineId` extended with `'genie-x'`.
- The native `GenieXBench` module is **designed but not written into the build**
  (see below), to avoid destabilizing the working 3-engine app with an untestable
  dependency.

## Native module design (GenieXBenchModule.kt)

From the GENIE-X Kotlin API (`com.geniex.sdk`):

```kotlin
// build.gradle: implementation("com.qualcomm.qti:geniex-android:0.3.1")
// (Kotlin 2.3.0 already set for LiteRT-LM — likely compatible.)

class GenieXBenchModule(ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {
  override fun getName() = "GenieXBench"

  @ReactMethod
  fun bench(modelPath: String, backend: String, prefillTokens: Int, decodeTokens: Int, promise: Promise) {
    thread {
      runBlocking {
        try {
          // 1. one-time SDK init (registers qairt + llama_cpp plugins)
          GenieXSdk.getInstance().init(reactApplicationContext) { /* InitCallback */ }
          // 2. compute unit: "cpu" | "gpu" | "htp" (Hexagon). backend->computeUnit map.
          val computeUnit = when (backend) { "npu" -> "htp"; "gpu" -> "gpu"; else -> "cpu" }
          val input = LlmCreateInput(
            model_path = modelPath,
            config = ModelConfig(/* ctx len, etc. — TBD from ModelConfig.kt */),
            compute_unit = computeUnit,
            runtime_id = if (backend == "npu") GenieXSdk.PLUGIN_ID_QAIRT else GenieXSdk.PLUGIN_ID_LLAMA_CPP,
          )
          val wrapper = LlmWrapper.builder().llmCreateInput(input).build().getOrThrow()
          // 3. time generateStreamFlow: first token = TTFT, count tokens for decode t/s.
          val t0 = System.currentTimeMillis(); var tFirst = 0L; var n = 0
          val prompt = /* standard prompt or synthetic */ "…"
          wrapper.generateStreamFlow(prompt, GenerationConfig(maxTokens = decodeTokens)).collect { r ->
            if (r is LlmStreamResult.Token) { if (tFirst == 0L) tFirst = System.currentTimeMillis(); n++ }
            // LlmGenerateResult in onComplete MAY expose engine counters — prefer those if present.
          }
          val end = System.currentTimeMillis()
          promise.resolve(Arguments.createMap().apply {
            putDouble("ttftMs", (tFirst - t0).toDouble())
            putDouble("decodeTps", if (end > tFirst) (n - 1) * 1000.0 / (end - tFirst) else 0.0)
            putInt("decodeTokens", n)
            putBoolean("engineCounters", false) // set true if LlmGenerateResult exposes tok/s
          })
          wrapper.close()
        } catch (e: Throwable) { promise.reject("GENIEX_ERR", e.message, e) }
      }
    }
  }
}
```

Open API questions to resolve on real hardware: `ModelConfig` required fields;
exact `compute_unit` string for Hexagon ("htp" vs "npu"); whether
`LlmGenerateResult` exposes prefill/decode counters (if so, use them → engine-timings).

## Steps to activate (when myron is unblocked + a QAIRT bundle is available)

1. On myron: Settings → Developer options → enable **Install via USB**.
2. `implementation("com.qualcomm.qti:geniex-android:0.3.1")` in `android/app/build.gradle`.
3. Write `GenieXBenchModule.kt` + `GenieXBenchPackage.kt` (per design above); register
   the package in `MainApplication.kt` (like `LiteRtBenchPackage`).
4. Get a QAIRT bundle for a small model from Qualcomm AI Hub (for the NPU path) and
   an ungated GGUF (for the CPU cross-check).
5. Add `GenieXAdapter` configs to `App.tsx` RUNS (npu + cpu).
6. Rebuild, stage models on myron, run `scripts/capture_app_run.sh <myron> 1200`.
7. Bench the **NPU (qairt) path** as the headline; the CPU path is a llama.cpp cross-check.

## Recommendation

GENIE-X belongs to **Phase C (NPU)**, not the CPU baseline. It should land together
with the other NPU paths (LiteRT-LM NPU, ExecuTorch QNN) once myron is unblocked, so
the whole NPU column appears at once on a Snapdragon device with proper NPU bundles.
Wiring it in now — untestable — would add build risk for zero data.
