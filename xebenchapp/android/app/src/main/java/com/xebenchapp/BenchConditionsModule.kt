package com.xebenchapp

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil
import com.facebook.react.bridge.WritableMap
import com.facebook.react.common.LifecycleState
import kotlin.concurrent.thread
import java.util.concurrent.atomic.AtomicBoolean

/** Endpoint observations only; unavailable APIs are null, never synthetic passes. */
class BenchConditionsModule(private val context: ReactApplicationContext) :
  ReactContextBaseJavaModule(context) {
  override fun getName() = "BenchConditions"
  private val sampling = AtomicBoolean(false)

  private fun WritableMap.number(key: String, read: () -> Double?) {
    val value = runCatching(read).getOrNull()
    if (value == null || !value.isFinite()) putNull(key) else putDouble(key, value)
  }

  private fun WritableMap.boolean(key: String, read: () -> Boolean?) {
    val value = runCatching(read).getOrNull()
    if (value == null) putNull(key) else putBoolean(key, value)
  }

  @ReactMethod
  fun sample(promise: Promise) {
    // A timed-out JS caller cannot spawn additional workers on a later invocation.
    if (!sampling.compareAndSet(false, true)) {
      promise.resolve(Arguments.createMap().apply { putString("probeStatus", "busy") })
      return
    }
    UiThreadUtil.runOnUiThread {
      val timestamp = System.currentTimeMillis().toDouble()
      val foreground = runCatching {
        context.lifecycleState == LifecycleState.RESUMED && context.currentActivity?.hasWindowFocus() == true
      }.getOrNull()
      // PSS can be slow: keep it off the UI thread and outside measured inference.
      thread(name = "xebench-conditions") {
        val result = runCatching {
          val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
          val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
          val battery = runCatching {
            context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
          }.getOrNull()
          Arguments.createMap().apply {
            putDouble("timestampMs", timestamp)
            boolean("foreground") { foreground }
            number("batteryPct") {
              val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
              val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
              if (level >= 0 && scale > 0) 100.0 * level / scale else null
            }
            number("batteryTempC") {
              if (battery?.hasExtra(BatteryManager.EXTRA_TEMPERATURE) == true)
                battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0 else null
            }
            boolean("charging") {
              // Includes external power while full, not just active battery charging.
              val plugged = battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1) ?: -1
              if (plugged >= 0) plugged != 0 else null
            }
            boolean("powerSaveMode") { power?.isPowerSaveMode }
            boolean("screenOn") { power?.isInteractive }
            boolean("keyguardLocked") { keyguard?.isKeyguardLocked }
            number("thermalStatus") {
              if (Build.VERSION.SDK_INT >= 29) power?.currentThermalStatus?.toDouble() else null
            }
            number("pssMb") {
              val memory = Debug.MemoryInfo()
              Debug.getMemoryInfo(memory)
              memory.totalPss / 1024.0
            }
          }
        }.getOrElse { Arguments.createMap() }
        sampling.set(false)
        promise.resolve(result)
      }
    }
  }
}
