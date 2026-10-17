package com.xebenchapp

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.facebook.react.common.LifecycleState
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread

/** Condition observations only; unavailable APIs are null, never synthetic passes. */
class BenchConditionsModule(private val context: ReactApplicationContext) :
  ReactContextBaseJavaModule(context) {
  override fun getName() = "BenchConditions"
  private val sampling = AtomicBoolean(false)
  private val lastHeadroomMs = AtomicLong(Long.MIN_VALUE / 2)
  private val monitorLock = Any()
  private var monitor: Monitor? = null

  private class Monitor(val intervalMs: Long, val maxDurationMs: Long, val maxSamples: Int) {
    val samples = ArrayList<WritableMap>()
    @Volatile var stopped = false
    var expired = false
    var ticks = 0
    var dropped = 0
    var maxCostNs = 0L
    var totalCostNs = 0L
    lateinit var worker: Thread
  }

  private fun WritableMap.number(key: String, read: () -> Double?) {
    val value = runCatching(read).getOrNull()
    if (value == null || !value.isFinite()) putNull(key) else putDouble(key, value)
  }

  private fun WritableMap.boolean(key: String, read: () -> Boolean?) {
    val value = runCatching(read).getOrNull()
    if (value == null) putNull(key) else putBoolean(key, value)
  }

  /**
   * Cheap reads only (no PSS walk), callable from a background thread. Window focus is
   * maintained by MainActivity on the UI thread; the lifecycle state is a field read.
   */
  private fun snapshot(includePss: Boolean): WritableMap {
    val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
    val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
    val battery = runCatching {
      context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    }.getOrNull()
    return Arguments.createMap().apply {
      putDouble("timestampMs", System.currentTimeMillis().toDouble())
      boolean("foreground") {
        context.lifecycleState == LifecycleState.RESUMED && MainActivity.windowFocused
      }
      number("focusLossCount") { MainActivity.focusLosses.get().toDouble() }
      number("processUptimeMs") {
        (SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime()).toDouble()
      }
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
      number("thermalHeadroom") {
        // NaN (null here) when unsupported or polled faster than about once per second.
        val now = SystemClock.elapsedRealtime()
        val last = lastHeadroomMs.get()
        if (Build.VERSION.SDK_INT >= 30 && now - last >= 1000 && lastHeadroomMs.compareAndSet(last, now))
          power?.getThermalHeadroom(0)?.toDouble() else null
      }
      number("rssMb") {
        File("/proc/self/status").useLines { lines ->
          lines.firstOrNull { it.startsWith("VmRSS:") }
            ?.split(Regex("\\s+"))?.getOrNull(1)?.toDoubleOrNull()?.div(1024.0)
        }
      }
      if (includePss) number("pssMb") {
        val memory = Debug.MemoryInfo()
        Debug.getMemoryInfo(memory)
        memory.totalPss / 1024.0
      }
    }
  }

  @ReactMethod
  fun sample(promise: Promise) {
    // A timed-out JS caller cannot spawn additional workers on a later invocation.
    if (!sampling.compareAndSet(false, true)) {
      promise.resolve(Arguments.createMap().apply { putString("probeStatus", "busy") })
      return
    }
    // PSS can be slow: keep it off the UI thread and outside measured inference.
    thread(name = "xebench-conditions") {
      val result = runCatching { snapshot(includePss = true) }.getOrElse { Arguments.createMap() }
      sampling.set(false)
      promise.resolve(result)
    }
  }

  /**
   * Low-rate background sampling while a model loads and runs. Each tick does a few
   * binder calls and one /proc read; no JS-bridge traffic happens until stopMonitor.
   * A monitor that is never stopped expires after maxDurationMs and stays busy until
   * stopped, so a lost stop request cannot silently overlap a later measurement.
   */
  @ReactMethod
  fun startMonitor(intervalMs: Double, maxDurationMs: Double, promise: Promise) {
    synchronized(monitorLock) {
      if (monitor != null || sampling.get()) {
        promise.resolve(Arguments.createMap().apply { putString("status", "busy") })
        return
      }
      val interval = intervalMs.toLong().coerceIn(250L, 60_000L)
      val duration = maxDurationMs.toLong().coerceIn(interval, 3_600_000L)
      val active = Monitor(interval, duration, (duration / interval + 2).toInt())
      active.worker = thread(name = "xebench-monitor", start = false) {
        val start = SystemClock.elapsedRealtime()
        while (!active.stopped) {
          try {
            Thread.sleep(active.intervalMs)
          } catch (_: InterruptedException) {
            break
          }
          if (active.stopped) break
          if (SystemClock.elapsedRealtime() - start > active.maxDurationMs) {
            active.expired = true
            break
          }
          val began = System.nanoTime()
          val sample = runCatching { snapshot(includePss = false) }.getOrElse { Arguments.createMap() }
          val cost = System.nanoTime() - began
          synchronized(active) {
            active.ticks++
            active.totalCostNs += cost
            active.maxCostNs = maxOf(active.maxCostNs, cost)
            if (active.samples.size < active.maxSamples) active.samples.add(sample) else active.dropped++
          }
        }
      }
      monitor = active
      active.worker.start()
      promise.resolve(Arguments.createMap().apply { putString("status", "started") })
    }
  }

  @ReactMethod
  fun stopMonitor(promise: Promise) {
    val active = synchronized(monitorLock) { monitor }
    if (active == null) {
      promise.resolve(Arguments.createMap().apply { putString("status", "not-running") })
      return
    }
    active.stopped = true
    active.worker.interrupt()
    thread(name = "xebench-monitor-stop") {
      active.worker.join(1000)
      if (active.worker.isAlive) {
        // Keep the monitor registered so later starts report busy instead of overlapping.
        promise.resolve(Arguments.createMap().apply { putString("status", "stuck") })
        return@thread
      }
      val result = Arguments.createMap()
      synchronized(active) {
        result.putString("status", "stopped")
        result.putDouble("intervalMs", active.intervalMs.toDouble())
        result.putInt("ticks", active.ticks)
        result.putInt("dropped", active.dropped)
        result.putBoolean("expired", active.expired)
        result.putDouble("maxSampleCostMs", active.maxCostNs / 1e6)
        result.putDouble("totalSampleCostMs", active.totalCostNs / 1e6)
        val samples = Arguments.createArray()
        active.samples.forEach { samples.pushMap(it) }
        result.putArray("samples", samples)
      }
      synchronized(monitorLock) { if (monitor === active) monitor = null }
      promise.resolve(result)
    }
  }
}
