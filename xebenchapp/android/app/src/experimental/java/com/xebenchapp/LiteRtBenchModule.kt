package com.xebenchapp

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.SamplerConfig
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Native bridge to LiteRT-LM's Engine/Conversation API for the accelerator protocol
 * (docs/experiments/litert-accelerators.md).
 *
 * One engine at a time, all calls on one worker thread, so load -> generate -> release
 * mirrors the harness's fresh-instance protocol. Measurement uses the engine's own
 * counters (ExperimentalFlags.enableBenchmark -> Conversation.getBenchmarkInfo()), on a
 * real chat-templated prompt, so prefill/decode tok/s come from LiteRT-LM itself.
 *
 * LiteRT-LM does not report which accelerator executed. load() therefore returns the
 * accelerator-relevant native libraries mapped in this process before and after engine
 * creation, read from /proc/self/maps. That is evidence the runtime was loaded, not
 * proof of execution; the harness labels it as such.
 */
class LiteRtBenchModule(private val reactContext: ReactApplicationContext) :
  ReactContextBaseJavaModule(reactContext) {

  private val worker = Executors.newSingleThreadExecutor { Thread(it, "litert-bench") }
  private var engine: Engine? = null

  override fun getName() = "LiteRtBench"

  private fun run(promise: Promise, code: String, body: () -> Any?) {
    worker.execute {
      try {
        promise.resolve(body())
      } catch (e: Throwable) {
        promise.reject(code, e.message ?: e.toString(), e)
      }
    }
  }

  @OptIn(ExperimentalApi::class)
  @ReactMethod
  fun load(modelPath: String, backend: String, threads: Int, promise: Promise) = run(promise, "LITERT_LOAD") {
    check(engine == null) { "A LiteRT-LM engine is already loaded" }
    val nld = reactContext.applicationInfo.nativeLibraryDir
    val be = when (backend) {
      "cpu" -> if (threads > 0) Backend.CPU(threads) else Backend.CPU()
      "gpu" -> Backend.GPU()
      "npu" -> {
        // FastRPC finds the Hexagon skel via ADSP_LIBRARY_PATH. llama.rn points it at its
        // own HTP dir (no QNN skel), so put nativeLibraryDir (QNN skels) first and keep
        // what is already there for llama.rn, then the system DSP dirs.
        // Os.getenv reads the live libc environment (System.getenv is a startup snapshot).
        val existing = android.system.Os.getenv("ADSP_LIBRARY_PATH")?.split(';')?.filter { it.isNotEmpty() } ?: emptyList()
        val dirs = (listOf(nld) + existing + listOf("/vendor/dsp/cdsp", "/vendor/lib/rfsa/adsp", "/dsp")).distinct()
        android.system.Os.setenv("ADSP_LIBRARY_PATH", dirs.joinToString(";"), true)
        Backend.NPU(nld)
      }
      else -> throw IllegalArgumentException("Unknown LiteRT-LM backend: $backend")
    }
    // Read at engine creation; enables Conversation.getBenchmarkInfo().
    ExperimentalFlags.enableBenchmark = true
    val before = acceleratorLibraries()
    val cache = File(reactContext.cacheDir, "litert").apply { mkdirs() }
    val t0 = System.nanoTime()
    val created = Engine(EngineConfig(modelPath = modelPath, backend = be, cacheDir = cache.absolutePath))
    try {
      created.initialize()
    } catch (e: Throwable) {
      // Keep the initialization error; a failing close must not replace it.
      runCatching { created.close() }.exceptionOrNull()?.let { e.addSuppressed(it) }
      throw e
    }
    val loadMs = (System.nanoTime() - t0) / 1e6
    engine = created
    Arguments.createMap().apply {
      putDouble("loadMs", loadMs)
      // null (not empty) when /proc/self/maps is unreadable: unknown stays unknown.
      putLibraries(this, "librariesBefore", before)
      putLibraries(this, "librariesAfter", acceleratorLibraries())
    }
  }

  /**
   * One greedy single-turn conversation on the loaded engine. stream=true measures
   * request -> first non-empty callback on this thread's clock (text not collected);
   * stream=false returns the full reply text (output-correctness gate).
   */
  @OptIn(ExperimentalApi::class)
  @ReactMethod
  fun generate(text: String, maxOutputTokens: Int, stream: Boolean, promise: Promise) = run(promise, "LITERT_GENERATE") {
    val loaded = checkNotNull(engine) { "No LiteRT-LM engine is loaded" }
    val config = ConversationConfig(
      samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
      maxOutputToken = maxOutputTokens,
    )
    loaded.createConversation(config).use { conversation ->
      val result = Arguments.createMap()
      val t0 = System.nanoTime()
      if (stream) {
        val done = CountDownLatch(1)
        var failure: Throwable? = null
        var first = -1L
        var chunks = 0
        conversation.sendMessageAsync(text, object : MessageCallback {
          override fun onMessage(message: Message) {
            if (textOf(message).isNotEmpty()) {
              if (first < 0) first = System.nanoTime()
              chunks++
            }
          }
          override fun onDone() = done.countDown()
          override fun onError(throwable: Throwable) {
            failure = throwable
            done.countDown()
          }
        })
        if (!done.await(10, TimeUnit.MINUTES)) {
          conversation.cancelProcess()
          // Let native decode stop before the conversation is closed.
          done.await(30, TimeUnit.SECONDS)
          throw IllegalStateException("LiteRT-LM generation timed out")
        }
        failure?.let { throw it }
        check(first >= 0) { "LiteRT-LM produced no output" }
        result.putDouble("ttftMs", (first - t0) / 1e6)
        result.putInt("chunks", chunks)
      } else {
        result.putString("text", textOf(conversation.sendMessage(text)))
      }
      result.putDouble("totalMs", (System.nanoTime() - t0) / 1e6)
      putBenchmark(result, conversation)
      result
    }
  }

  @ReactMethod
  fun release(promise: Promise) = run(promise, "LITERT_RELEASE") {
    engine?.close()
    engine = null
    null
  }

  @OptIn(ExperimentalApi::class)
  private fun putBenchmark(result: WritableMap, conversation: com.google.ai.edge.litertlm.Conversation) {
    try {
      val info = conversation.getBenchmarkInfo()
      result.putMap("benchmark", Arguments.createMap().apply {
        putDouble("timeToFirstTokenMs", info.timeToFirstTokenInSecond * 1000.0)
        putInt("prefillTokens", info.lastPrefillTokenCount)
        putDouble("prefillTps", info.lastPrefillTokensPerSecond)
        putInt("decodeTokens", info.lastDecodeTokenCount)
        putDouble("decodeTps", info.lastDecodeTokensPerSecond)
      })
    } catch (e: Throwable) {
      result.putNull("benchmark")
      result.putString("benchmarkError", e.message ?: e.toString())
    }
  }

  private fun textOf(message: Message): String =
    message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }

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
    private val MARKERS = listOf("libOpenCL", "libLiteRt", "libQnn", "libcdsprpc", "libadsprpc", "libvndksupport")
  }
}
