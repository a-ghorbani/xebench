package com.xebenchapp

import com.google.ai.edge.litertlm.BenchmarkInfo
import com.google.ai.edge.litertlm.NoRepeatNgramConfig
import com.google.ai.edge.litertlm.RepetitionPenaltyConfig
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import org.json.JSONArray
import org.json.JSONObject
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Method
import java.lang.reflect.Proxy

/**
 * Fixed-length decoding for LiteRT-LM 0.18.0 (configuration decodeMode "fixed").
 *
 * LiteRT-LM's public Kotlin API cannot decode past the end of turn: stop tokens come from the
 * artifact's metadata, and suppressed tokens are not applied by the NPU executor. The engine decodes
 * a fixed number of steps and ignores stop tokens only when its BenchmarkParams set
 * num_decode_tokens (runtime/core/tasks.cc ShouldStop), which only nativeCreateBenchmark does. With
 * num_prefill_tokens 0 the real prompt is tokenized as usual (session_utils.cc
 * StringToProcessedInputText resizes the prompt only for a positive count). CPU runs use the
 * engine's default of 4 threads (llm_executor_settings.h); nativeCreateBenchmark takes no count.
 *
 * LiteRtLmJni is internal to the litertlm-android module, so Kotlin cannot name it; its natives
 * are public in the bytecode and are called here by reflection, with the argument lists of
 * litertlm-android 0.18.0 exactly. [requirePinned] refuses any other version and any missing
 * entry point, so a LiteRT-LM update fails loudly instead of measuring something else.
 */
internal object LiteRtFixedDecode {
  const val PINNED_LITERTLM_VERSION = "0.18.0"
  private const val JNI_CLASS = "com.google.ai.edge.litertlm.LiteRtLmJni"
  private const val CALLBACK_CLASS = "com.google.ai.edge.litertlm.LiteRtLmJni\$JniMessageCallback"

  private class Natives(
    val instance: Any,
    val callbackType: Class<*>,
    val createBenchmark: Method,
    val createConversation: Method,
    val sendMessageAsync: Method,
    val render: Method,
    val benchmarkInfo: Method,
    val cancel: Method,
    val deleteConversation: Method,
    val deleteEngine: Method,
  )

  private var natives: Natives? = null

  /** Throws unless the packaged litertlm-android is the version these calls were written for. */
  fun requirePinned(packagedVersion: String) {
    check(packagedVersion == PINNED_LITERTLM_VERSION) {
      "Fixed-length decoding is pinned to litertlm-android $PINNED_LITERTLM_VERSION; this APK packages $packagedVersion"
    }
    natives()
  }

  private fun natives(): Natives = natives ?: try {
    val jni = Class.forName(JNI_CLASS)
    val callback = Class.forName(CALLBACK_CLASS)
    fun method(name: String, vararg types: Class<*>) = jni.getMethod(name, *types)
    val long = java.lang.Long.TYPE
    val int = Integer.TYPE
    val bool = java.lang.Boolean.TYPE
    val string = String::class.java
    val boxedBool = java.lang.Boolean::class.java
    Natives(
      instance = jni.getField("INSTANCE").get(null)!!,
      callbackType = callback,
      createBenchmark = method("nativeCreateBenchmark", string, string, int, int, string, string, boxedBool),
      createConversation = method("nativeCreateConversation", long, SamplerConfig::class.java, string, string,
        string, string, bool, boxedBool, string, string, string, bool, int, ThinkingConfig::class.java, bool,
        boxedBool),
      sendMessageAsync = method("nativeSendMessageAsync", long, string, string, callback,
        Integer::class.java, RepetitionPenaltyConfig::class.java, NoRepeatNgramConfig::class.java,
        IntArray::class.java, int, ThinkingConfig::class.java, int, string),
      render = method("nativeConversationRenderMessageIntoString", long, string, string),
      benchmarkInfo = method("nativeConversationGetBenchmarkInfo", long),
      cancel = method("nativeConversationCancelProcess", long),
      deleteConversation = method("nativeDeleteConversation", long),
      deleteEngine = method("nativeDeleteEngine", long),
    ).also { natives = it }
  } catch (e: ReflectiveOperationException) {
    throw IllegalStateException("litertlm-android lacks a JNI entry point fixed-length decoding needs: $e", e)
  }

  /** Invokes a native, rethrowing what the native threw (LiteRtLmJniException), not the reflection wrapper. */
  private fun call(method: Method, vararg args: Any?): Any? = try {
    method.invoke(natives().instance, *args)
  } catch (e: InvocationTargetException) {
    throw e.targetException ?: e
  }

  /** An initialized engine that decodes exactly decodeTokens steps per request. */
  fun createEngine(modelPath: String, backend: String, decodeTokens: Int, cacheDir: String, npuLibraryDir: String): Long {
    require(decodeTokens > 0) { "decodeTokens must be positive" }
    return call(natives().createBenchmark, modelPath, backend, 0, decodeTokens, cacheDir, npuLibraryDir, null) as Long
  }

  /** A greedy conversation; chatTemplate null keeps the artifact's own template. */
  fun createConversation(engine: Long, chatTemplate: String?, maxOutputTokens: Int): Long =
    call(natives().createConversation, engine, SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
      "[]", "[]", null, "{}", false, null, chatTemplate, null, null, false, maxOutputTokens, null, false, null) as Long

  /** A user message as Message.toJson() writes it: {"role":"user","content":[{"type":"text","text":…}]}. */
  private fun userMessage(text: String): String {
    val part = JSONObject().put("type", "text").put("text", text)
    return JSONObject().put("role", "user").put("content", JSONArray().put(part)).toString()
  }

  fun render(conversation: Long, text: String): String =
    call(natives().render, conversation, userMessage(text), "{}") as String

  /** Streamed reply events. */
  interface Stream {
    fun onText(text: String)
    fun onDone()
    fun onError(statusCode: Int, message: String)
  }

  fun send(conversation: Long, text: String, maxOutputTokens: Int, stream: Stream) {
    val type = natives().callbackType
    val callback = Proxy.newProxyInstance(type.classLoader, arrayOf(type)) { proxy, method, args ->
      when (method.name) {
        "onMessage" -> stream.onText(textOf(args[0] as String))
        "onDone" -> stream.onDone()
        "onError" -> stream.onError(args[0] as Int, args[1] as String)
        "hashCode" -> return@newProxyInstance System.identityHashCode(proxy)
        "equals" -> return@newProxyInstance proxy === args[0]
        "toString" -> return@newProxyInstance "LiteRtFixedDecode.Stream"
      }
      null
    }
    call(natives().sendMessageAsync, conversation, userMessage(text), "{}", callback, null, null, null, null,
      maxOutputTokens, null, 0, null)
  }

  /** Text of one streamed message JSON (its text parts joined); empty when it carries none. */
  fun textOf(messageJson: String): String = runCatching {
    val content = JSONObject(messageJson).optJSONArray("content") ?: return@runCatching ""
    (0 until content.length()).mapNotNull { content.optJSONObject(it) }
      .filter { it.optString("type") == "text" }.joinToString("") { it.optString("text") }
  }.getOrDefault("")

  fun benchmarkInfo(conversation: Long): BenchmarkInfo = call(natives().benchmarkInfo, conversation) as BenchmarkInfo

  fun cancel(conversation: Long) {
    call(natives().cancel, conversation)
  }

  fun deleteConversation(conversation: Long) {
    call(natives().deleteConversation, conversation)
  }

  fun deleteEngine(engine: Long) {
    call(natives().deleteEngine, engine)
  }
}
