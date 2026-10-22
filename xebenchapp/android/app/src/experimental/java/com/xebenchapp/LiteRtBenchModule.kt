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
 * real prompt (chat-templated, or pre-templated and rendered verbatim), so prefill/decode
 * tok/s come from LiteRT-LM itself.
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
  /** decodeMode fixed: a native engine handle from LiteRtFixedDecode (0 when none is loaded). */
  private var fixedEngine = 0L
  private var fixedDecodeTokens = 0

  override fun getName() = "LiteRtBench"

  /**
   * Versions of the runtimes this APK packages, from the shipped artifacts: the AARs as
   * Gradle resolved them (BuildConfig), and the QAIRT build string embedded in the packaged
   * libQnnHtp.so. Absent (null) when a library is not packaged or carries no version.
   */
  override fun getConstants(): Map<String, Any?> = mapOf(
    "litertLmVersion" to BuildConfig.XEBENCH_LITERTLM_VERSION.ifEmpty { null },
    "litertNpuRuntimeQualcommVersion" to BuildConfig.XEBENCH_LITERT_NPU_QUALCOMM_VERSION.ifEmpty { null },
    "qairtVersion" to qairtVersion(),
  )

  /** QAIRT build string (e.g. 2.50.0.260828221209) from libQnnHtp.so's own "vX.Y.Z.BUILD" marker. */
  private fun qairtVersion(): String? = runCatching {
    val lib = File(reactContext.applicationInfo.nativeLibraryDir, "libQnnHtp.so")
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

  @OptIn(ExperimentalApi::class)
  @ReactMethod
  fun load(modelPath: String, backend: String, threads: Int, decodeTokens: Int, promise: Promise) =
    run(promise, "LITERT_LOAD") {
    check(engine == null && fixedEngine == 0L) { "A LiteRT-LM engine is already loaded" }
    if (decodeTokens > 0) {
      LiteRtFixedDecode.requirePinned(BuildConfig.XEBENCH_LITERTLM_VERSION)
      // nativeCreateBenchmark takes no thread count; the engine's CPU default is 4.
      check(backend != "cpu" || threads <= 0 || threads == 4) { "Fixed-length decoding runs CPU on 4 threads only" }
    }
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
    if (decodeTokens > 0) {
      // Created and initialized in one native call; the same backend names as Backend.name.
      val npuDir = if (be is Backend.NPU) be.nativeLibraryDir else ""
      fixedEngine = LiteRtFixedDecode.createEngine(modelPath, be.name, decodeTokens, cache.absolutePath, npuDir)
      check(fixedEngine != 0L) { "LiteRT-LM did not create the fixed-length engine" }
      fixedDecodeTokens = decodeTokens
    } else {
      val created = Engine(EngineConfig(modelPath = modelPath, backend = be, cacheDir = cache.absolutePath))
      try {
        created.initialize()
      } catch (e: Throwable) {
        // Keep the initialization error; a failing close must not replace it.
        runCatching { created.close() }.exceptionOrNull()?.let { e.addSuppressed(it) }
        throw e
      }
      engine = created
    }
    val loadMs = (System.nanoTime() - t0) / 1e6
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
   *
   * raw=false sends text as a user message in the artifact's own chat template. raw=true
   * sends text as an already templated prompt (std-pp512-v1): the conversation's template is
   * replaced by RAW_TEMPLATE, which renders the user message verbatim, and the rendered
   * prompt must equal text byte for byte before anything is prefilled. LiteRT-LM still adds
   * the artifact's start token on the first turn when its metadata defines one.
   */
  @OptIn(ExperimentalApi::class)
  @ReactMethod
  fun generate(text: String, maxOutputTokens: Int, stream: Boolean, raw: Boolean, promise: Promise) =
    run(promise, "LITERT_GENERATE") {
    if (fixedEngine != 0L) return@run generateFixed(text, maxOutputTokens, stream, raw)
    val loaded = checkNotNull(engine) { "No LiteRT-LM engine is loaded" }
    val config = ConversationConfig(
      samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
      maxOutputToken = maxOutputTokens,
      chatTemplate = if (raw) RAW_TEMPLATE else null,
    )
    loaded.createConversation(config).use { conversation ->
      val result = Arguments.createMap()
      if (raw) {
        val rendered = conversation.renderMessageIntoString(Message.user(text))
        check(rendered == text) {
          "LiteRT-LM rendered the raw prompt differently (${rendered.length} vs ${text.length} chars)"
        }
        result.putBoolean("rawPromptVerified", true)
      }
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

  /**
   * generate() on a fixed-length engine (decodeMode fixed): the same request, render check and
   * timing, through LiteRtFixedDecode's JNI calls. The engine decodes exactly fixedDecodeTokens
   * steps, past the end of turn, and the result must report that many decode tokens.
   */
  private fun generateFixed(text: String, maxOutputTokens: Int, stream: Boolean, raw: Boolean): WritableMap {
    check(stream) { "A fixed-length engine is for measured repetitions only, not for the output gate" }
    check(maxOutputTokens == fixedDecodeTokens) { "Decode length $maxOutputTokens is not the engine's $fixedDecodeTokens" }
    val conversation = LiteRtFixedDecode.createConversation(fixedEngine, if (raw) RAW_TEMPLATE else null, maxOutputTokens)
    check(conversation != 0L) { "LiteRT-LM did not create a conversation" }
    try {
      val result = Arguments.createMap()
      if (raw) {
        val rendered = LiteRtFixedDecode.render(conversation, text)
        check(rendered == text) {
          "LiteRT-LM rendered the raw prompt differently (${rendered.length} vs ${text.length} chars)"
        }
        result.putBoolean("rawPromptVerified", true)
      }
      val done = CountDownLatch(1)
      var failure: Throwable? = null
      var first = -1L
      var chunks = 0
      val t0 = System.nanoTime()
      LiteRtFixedDecode.send(conversation, text, maxOutputTokens, object : LiteRtFixedDecode.Stream {
        override fun onText(text: String) {
          if (text.isNotEmpty()) {
            if (first < 0) first = System.nanoTime()
            chunks++
          }
        }
        override fun onDone() = done.countDown()
        override fun onError(statusCode: Int, message: String) {
          failure = IllegalStateException("LiteRT-LM status $statusCode: $message")
          done.countDown()
        }
      })
      if (!done.await(10, TimeUnit.MINUTES)) {
        LiteRtFixedDecode.cancel(conversation)
        done.await(30, TimeUnit.SECONDS)
        throw IllegalStateException("LiteRT-LM generation timed out")
      }
      failure?.let { throw it }
      check(first >= 0) { "LiteRT-LM produced no output" }
      result.putDouble("ttftMs", (first - t0) / 1e6)
      result.putInt("chunks", chunks)
      result.putDouble("totalMs", (System.nanoTime() - t0) / 1e6)
      val info = LiteRtFixedDecode.benchmarkInfo(conversation)
      check(info.lastDecodeTokenCount == fixedDecodeTokens) {
        "LiteRT-LM decoded ${info.lastDecodeTokenCount} tokens, not the fixed $fixedDecodeTokens"
      }
      result.putMap("benchmark", benchmarkMap(info))
      result.putBoolean("fixedDecode", true)
      return result
    } finally {
      LiteRtFixedDecode.deleteConversation(conversation)
    }
  }

  @ReactMethod
  fun release(promise: Promise) = run(promise, "LITERT_RELEASE") {
    engine?.close()
    engine = null
    if (fixedEngine != 0L) {
      val handle = fixedEngine
      fixedEngine = 0L
      fixedDecodeTokens = 0
      LiteRtFixedDecode.deleteEngine(handle)
    }
    null
  }

  private fun benchmarkMap(info: com.google.ai.edge.litertlm.BenchmarkInfo) = Arguments.createMap().apply {
    putDouble("timeToFirstTokenMs", info.timeToFirstTokenInSecond * 1000.0)
    putInt("prefillTokens", info.lastPrefillTokenCount)
    putDouble("prefillTps", info.lastPrefillTokensPerSecond)
    putInt("decodeTokens", info.lastDecodeTokenCount)
    putDouble("decodeTps", info.lastDecodeTokensPerSecond)
  }

  @OptIn(ExperimentalApi::class)
  private fun putBenchmark(result: WritableMap, conversation: com.google.ai.edge.litertlm.Conversation) {
    try {
      result.putMap("benchmark", benchmarkMap(conversation.getBenchmarkInfo()))
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
    /**
     * Renders every message's text parts verbatim: no role markers, no generation prompt.
     * LiteRT-LM 0.18 passes message content as a list of parts; a plain string is accepted too.
     */
    private const val RAW_TEMPLATE =
      "{%- for message in messages -%}" +
        "{%- if message['content'] is string -%}{{ message['content'] }}" +
        "{%- else -%}{%- for part in message['content'] -%}" +
        "{%- if part['type'] == 'text' -%}{{ part['text'] }}{%- endif -%}" +
        "{%- endfor -%}{%- endif -%}" +
        "{%- endfor -%}"
    private val MARKERS = listOf("libOpenCL", "libLiteRt", "libQnn", "libcdsprpc", "libadsprpc", "libvndksupport")
  }
}
