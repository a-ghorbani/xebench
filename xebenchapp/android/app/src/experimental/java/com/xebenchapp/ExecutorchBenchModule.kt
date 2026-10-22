package com.xebenchapp

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import java.io.File

/**
 * What the experimental APK says about its ExecuTorch runtime; react-native-executorch itself
 * runs inference (ExecutorchAdapter.ts). Its presence marks a build that packages ExecuTorch:
 * the reference and geniex flavors exclude the native libraries and do not register it.
 *
 * - executorchVersion: ET_VERSION of the headers shipped with the downloaded ExecuTorch
 *   libraries, embedded at build time (app/build.gradle); "" when unknown.
 * - libraries(): ExecuTorch-family libraries mapped in this process (/proc/self/maps), so a
 *   record shows which backend libraries were loaded; rejected if the maps cannot be read.
 */
class ExecutorchBenchModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName() = "ExecutorchBench"

  override fun getConstants(): Map<String, Any> =
    mapOf("executorchVersion" to BuildConfig.XEBENCH_EXECUTORCH_VERSION)

  @ReactMethod
  fun libraries(promise: Promise) {
    try {
      val names = File("/proc/self/maps").readLines()
        .mapNotNull { line -> line.substringAfterLast(' ').takeIf { it.startsWith("/") && it.endsWith(".so") } }
        .map { it.substringAfterLast('/') }
        .filter { name -> MARKERS.any { it.matches(name) } }
        .distinct().sorted()
      promise.resolve(Arguments.fromList(names))
    } catch (error: Exception) {
      promise.reject("executorch_libraries", error)
    }
  }

  companion object {
    private val MARKERS = listOf(
      Regex("libexecutorch\\.so"),
      Regex("libreact-native-executorch\\.so"),
      Regex("lib[A-Za-z0-9]+_executorch_backend\\.so"),
      Regex("libQnn[A-Za-z0-9]*\\.so"),
    )
  }
}
