package com.xebenchapp

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.geniex.sdk.GenieXSdk
import com.geniex.sdk.LlmWrapper
import com.geniex.sdk.bean.GenerationConfig
import com.geniex.sdk.bean.LlmCreateInput
import com.geniex.sdk.bean.ModelConfig
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.runBlocking
import java.util.concurrent.CountDownLatch
import kotlin.concurrent.thread

/**
 * Native bridge to GENIE-X (Qualcomm). GENIE-X has no RN binding; this exposes
 * its LlmWrapper to JS. Snapdragon 8 Elite / 8 Elite Gen 5 ONLY — its NPU path
 * (runtime_id=qairt) is the reason it exists; the llama_cpp runtime_id is just
 * llama.cpp. Integrated at build level; runtime-testable only on a supported,
 * installable Snapdragon device (see GENIEX-INTEGRATION.md).
 */
class GenieXBenchModule(private val ctx: ReactApplicationContext) :
  ReactContextBaseJavaModule(ctx) {

  override fun getName() = "GenieXBench"

  @ReactMethod
  fun bench(modelPath: String, backend: String, prompt: String, maxTokens: Int, promise: Promise) {
    thread {
      try {
        // 1. one-time SDK init (registers qairt + llama_cpp plugins)
        val latch = CountDownLatch(1)
        val initErr = StringBuilder()
        GenieXSdk.getInstance().init(ctx, object : GenieXSdk.InitCallback {
          override fun onSuccess() { latch.countDown() }
          override fun onFailure(reason: String) { initErr.append(reason); latch.countDown() }
        })
        latch.await()
        if (initErr.isNotEmpty()) throw IllegalStateException("GenieX init: $initErr")

        // 2. map backend -> runtime + compute unit
        val npu = backend.equals("npu", true)
        val runtimeId = if (npu) "qairt" else GenieXSdk.PLUGIN_ID_LLAMA_CPP
        val computeUnit = when {
          npu -> "HTP0"
          backend.equals("gpu", true) -> "GPU0"
          else -> "CPU0"
        }
        val gpuLayers = if (backend.equals("gpu", true)) 99 else 0

        runBlocking {
          val wrapper = LlmWrapper.builder()
            .llmCreateInput(
              LlmCreateInput(
                model_path = modelPath,
                config = ModelConfig(nCtx = 2048, nGpuLayers = gpuLayers, max_tokens = maxTokens),
                runtime_id = runtimeId,
                compute_unit = computeUnit,
              ),
            ).build().getOrThrow()

          val t0 = System.currentTimeMillis()
          var tFirst = 0L
          var n = 0
          wrapper.generateStreamFlow(prompt, GenerationConfig(maxTokens = maxTokens)).collect {
            if (tFirst == 0L) tFirst = System.currentTimeMillis()
            n++
          }
          val end = System.currentTimeMillis()
          wrapper.close()

          val ttft = (tFirst - t0).coerceAtLeast(0)
          val decodeMs = (end - tFirst).coerceAtLeast(1)
          promise.resolve(Arguments.createMap().apply {
            putDouble("loadMs", 0.0)
            putDouble("ttftMs", ttft.toDouble())
            putInt("promptTokens", 0)
            putInt("decodeTokens", n)
            putDouble("prefillTps", 0.0) // no engine prefill counter surfaced
            putDouble("decodeTps", if (n > 1) (n - 1) * 1000.0 / decodeMs else 0.0)
            putBoolean("engineCounters", false)
          })
        }
      } catch (e: Throwable) {
        promise.reject("GENIEX_ERR", e.message ?: e.toString(), e)
      }
    }
  }
}
