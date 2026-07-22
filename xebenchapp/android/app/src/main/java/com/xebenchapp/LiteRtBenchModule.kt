package com.xebenchapp

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.benchmark
import kotlin.concurrent.thread

/**
 * Native bridge to LiteRT-LM's built-in benchmark().
 *
 * LiteRT-LM has no RN binding, so this thin legacy module exposes its
 * engine-reported prefill/decode tok/s (BenchmarkInfo) to JS. Runs in the
 * foreground app process (top-app cpuset -> real clocks; METHODOLOGY Rule 0).
 * The Backend enum (CPU/GPU/NPU) is the single lever for the whole backend
 * roadmap — same call, different backend arg.
 */
class LiteRtBenchModule(private val reactContext: ReactApplicationContext) :
  ReactContextBaseJavaModule(reactContext) {

  override fun getName() = "LiteRtBench"

  @OptIn(ExperimentalApi::class)
  @ReactMethod
  fun bench(
    modelPath: String,
    backend: String,
    prefillTokens: Int,
    decodeTokens: Int,
    promise: Promise,
  ) {
    thread {
      try {
        val be = when (backend.lowercase()) {
          "gpu" -> Backend.GPU()
          "npu" -> {
            // libQnnHtpV73Skel.so is extracted into nativeLibraryDir; FastRPC finds
            // the DSP skel via ADSP_LIBRARY_PATH. llama.rn hijacks that env var to its
            // own HTP dir (libggml-htp-*.so, no QNN skel), so force it here (+ system
            // DSP paths for the skel's firmware deps) right before QNN init.
            val nld = reactContext.applicationInfo.nativeLibraryDir
            val adsp = "$nld;/vendor/dsp/cdsp;/vendor/lib/rfsa/adsp;/dsp"
            try { android.system.Os.setenv("ADSP_LIBRARY_PATH", adsp, true) } catch (_: Throwable) {}
            android.util.Log.d("LiteRtBench", "ADSP_LIBRARY_PATH=$adsp")
            Backend.NPU(nld)
          }
          else -> Backend.CPU()
        }
        val info = benchmark(
          modelPath = modelPath,
          backend = be,
          prefillTokens = prefillTokens,
          decodeTokens = decodeTokens,
          cacheDir = reactContext.cacheDir.absolutePath,
        )
        val map = Arguments.createMap().apply {
          putDouble("loadMs", info.initTimeInSecond * 1000.0)
          putDouble("ttftMs", info.timeToFirstTokenInSecond * 1000.0)
          putInt("promptTokens", info.lastPrefillTokenCount)
          putInt("decodeTokens", info.lastDecodeTokenCount)
          putDouble("prefillTps", info.lastPrefillTokensPerSecond)
          putDouble("decodeTps", info.lastDecodeTokensPerSecond)
        }
        promise.resolve(map)
      } catch (e: Throwable) {
        promise.reject("LITERT_ERR", e.message ?: e.toString(), e)
      }
    }
  }
}
