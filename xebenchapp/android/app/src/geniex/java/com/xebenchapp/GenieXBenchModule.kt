package com.xebenchapp

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.geniex.sdk.GenieXSdk
import com.geniex.sdk.bean.ComputeUnitValue
import com.geniex.sdk.bean.GenerationConfig
import com.geniex.sdk.bean.LLMTokenCallback
import com.geniex.sdk.bean.LlmCreateInput
import com.geniex.sdk.bean.LlmGenerateResult
import com.geniex.sdk.bean.ModelConfig
import com.geniex.sdk.bean.ProfilingData
import com.geniex.sdk.bean.RuntimeIdValue
import com.geniex.sdk.bean.SamplerConfig
import java.io.File
import java.util.concurrent.Executors

/**
 * Native bridge to GENIE-X (Qualcomm geniex-android, Maven Central) for the accelerator protocol;
 * geniex flavor only. GENIE-X has no React Native binding.
 *
 * One model at a time, every call on one worker thread, so load -> generate -> release mirrors the
 * harness's fresh-instance protocol:
 *  - backend "npu": GENIE-X's `qairt` plugin on a QAIRT bundle (modelPath is the bundle's
 *    genie_config.json). The plugin loads the QNN runtime from the directory of libgeniex_core.so,
 *    which is this APK's native library directory, so the QAIRT version is the packaged one
 *    (qairtVersion below). geniex-android 0.8.0 cannot load another one: its JNI_OnLoad calls
 *    geniex_init, which locks GenieXSdk.setQairtRuntimePath before any app code can call it.
 *  - backend "cpu": GENIE-X's `llama_cpp` plugin on a GGUF (the output gate's reference).
 *
 * Requests are greedy (temperature below zero is the argmax sentinel of both plugins; 0 would
 * defer to the bundle's sampler, temperature 0.8), carry no stop words and send the prompt as the
 * raw prompt. GENIE-X stops at the end of turn; it has no option to decode past it.
 *
 * GENIE-X does not report which accelerator executed. load() returns the accelerator-relevant
 * native libraries mapped in this process before and after model creation (/proc/self/maps);
 * the app's log carries the stronger evidence (scripts/backend_log.py, geniex-npu-log-v1).
 */
class GenieXBenchModule(private val ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {

  private val worker = Executors.newSingleThreadExecutor { Thread(it, "geniex-bench") }
  private var native: GenieXNative? = null
  private var handle = 0L
  private var sdkReady = false

  override fun getName() = "GenieXBench"

  /**
   * Versions of the runtimes this APK packages: geniex-android as Gradle resolved it (BuildConfig),
   * and the QAIRT build string in the packaged libQnnHtp.so. Null when absent.
   */
  override fun getConstants(): Map<String, Any?> = mapOf(
    "geniexVersion" to BuildConfig.XEBENCH_GENIEX_VERSION.ifEmpty { null },
    "qairtVersion" to qairtVersion(),
  )

  /** QAIRT build string (e.g. 2.50.0.260828221209) from libQnnHtp.so's own "vX.Y.Z.BUILD" marker. */
  private fun qairtVersion(): String? = runCatching {
    val lib = File(ctx.applicationInfo.nativeLibraryDir, "libQnnHtp.so")
    if (!lib.isFile) return@runCatching null
    val text = String(lib.readBytes(), Charsets.ISO_8859_1)
    Regex("v([0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{6,14})(?![0-9])").find(text)?.groupValues?.get(1)
  }.getOrNull()

  private fun run(promise: Promise, code: String, body: () -> Any?) {
    worker.execute {
      try {
        promise.resolve(body())
      } catch (e: Throwable) {
        promise.reject(code, e.message ?: e.toString(), e)
      }
    }
  }

  /** Plugin registration and model-manager setup, once per process (GenieXSdk.init is synchronous). */
  private fun sdk(): GenieXNative {
    GenieXNative.requirePinned(BuildConfig.XEBENCH_GENIEX_VERSION)
    if (!sdkReady) {
      var failure: String? = null
      GenieXSdk.getInstance().init(ctx, object : GenieXSdk.InitCallback {
        override fun onSuccess() {}
        override fun onFailure(reason: String) {
          failure = reason
        }
      })
      failure?.let { throw IllegalStateException("GENIE-X init failed: ${it.trim()}") }
      sdkReady = true
    }
    return native ?: GenieXNative().also { native = it }
  }

  @ReactMethod
  fun load(modelPath: String, backend: String, threads: Int, nCtx: Int, promise: Promise) =
    run(promise, "GENIEX_LOAD") {
      check(handle == 0L) { "A GENIE-X model is already loaded" }
      val api = sdk()
      val input = when (backend) {
        // qairt refuses n_ctx and n_gpu_layers (the bundle fixes both); power mode "" is burst.
        "npu" -> LlmCreateInput(modelPath, null, ModelConfig(nCtx = 0, nGpuLayers = 0),
          RuntimeIdValue.QAIRT.value, ComputeUnitValue.NPU.value)
        "cpu" -> {
          require(threads > 0 && nCtx > 0) { "GENIE-X CPU runs need threads and a context size" }
          LlmCreateInput(modelPath, null, ModelConfig(nCtx = nCtx, nThreads = threads, nThreadsBatch = threads,
            nGpuLayers = 0), RuntimeIdValue.LLAMA_CPP.value, ComputeUnitValue.CPU.value)
        }
        else -> throw IllegalArgumentException("Unknown GENIE-X backend: $backend")
      }
      val before = acceleratorLibraries()
      val t0 = System.nanoTime()
      val created = api.create(input)
      val loadMs = (System.nanoTime() - t0) / 1e6
      check(created != 0L) { "GENIE-X did not create the model" }
      handle = created
      Arguments.createMap().apply {
        putDouble("loadMs", loadMs)
        putLibraries(this, "librariesBefore", before)
        putLibraries(this, "librariesAfter", acceleratorLibraries())
      }
    }

  /**
   * One greedy request from an empty context: the raw prompt, at most maxTokens decoded. Returns the
   * reply text, the time from the request to the first non-empty token callback on this thread, and
   * GENIE-X's ProfilingData.
   */
  @ReactMethod
  fun generate(prompt: String, maxTokens: Int, promise: Promise) = run(promise, "GENIEX_GENERATE") {
    val api = checkNotNull(native) { "GENIE-X is not initialized" }
    check(handle != 0L) { "No GENIE-X model is loaded" }
    check(maxTokens > 0) { "maxTokens must be positive" }
    check(api.reset(handle) == 0) { "GENIE-X could not reset the model context" }
    val config = GenerationConfig(maxTokens = maxTokens, stopWords = null, stopCount = 0,
      samplerConfig = SamplerConfig(temperature = -1.0f, topP = 1.0f, topK = 1, seed = 42))
    val text = StringBuilder()
    var first = -1L
    var completed: LlmGenerateResult? = null
    val t0 = System.nanoTime()
    api.generate(handle, prompt, config, object : LLMTokenCallback {
      override fun onToken(token: String): Boolean {
        if (token.isNotEmpty()) {
          if (first < 0) first = System.nanoTime()
          text.append(token)
        }
        return true
      }

      override fun onComplete(result: LlmGenerateResult) {
        completed = result
      }
    })
    val result = checkNotNull(completed) { "GENIE-X finished without a result" }
    Arguments.createMap().apply {
      putString("text", result.fullText.ifEmpty { text.toString() })
      if (first >= 0) putDouble("ttftMs", (first - t0) / 1e6) else putNull("ttftMs")
      putMap("profile", profileMap(result.profileData))
    }
  }

  @ReactMethod
  fun release(promise: Promise) = run(promise, "GENIEX_RELEASE") {
    if (handle != 0L) {
      val closing = handle
      handle = 0L
      val code = checkNotNull(native).destroy(closing)
      check(code == 0) { "GENIE-X destroy returned $code" }
    }
    null
  }

  private fun profileMap(profile: ProfilingData): WritableMap = Arguments.createMap().apply {
    putDouble("ttftMs", profile.ttftMs)
    putDouble("promptTimeMs", profile.promptTimeMs)
    putDouble("decodeTimeMs", profile.decodeTimeMs)
    putDouble("promptTokens", profile.promptTokens.toDouble())
    putDouble("generatedTokens", profile.generatedTokens.toDouble())
    putDouble("prefillSpeed", profile.prefillSpeed)
    putDouble("decodingSpeed", profile.decodingSpeed)
    putString("stopReason", profile.stopReason)
  }

  private fun putLibraries(map: WritableMap, key: String, names: List<String>?) {
    if (names == null) map.putNull(key) else map.putArray(key, Arguments.fromList(names))
  }

  /** Basenames of mapped native libraries that identify an accelerator runtime; null if unreadable. */
  private fun acceleratorLibraries(): List<String>? = try {
    File("/proc/self/maps").readLines()
      .mapNotNull { line -> line.substringAfterLast(' ').takeIf { it.endsWith(".so") }?.substringAfterLast('/') }
      .filter { name -> MARKERS.any { name.startsWith(it) } }
      .distinct().sorted()
  } catch (_: Throwable) {
    null
  }

  companion object {
    private val MARKERS = listOf("libQnn", "libcdsprpc", "libadsprpc", "libgeniex", "libggml", "libOpenCL")
  }
}
