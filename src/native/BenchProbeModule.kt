package com.xebench.benchprobe

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.app.ActivityManager
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil
import java.io.File

/**
 * xebench measurement probe: memory, thermal, battery, device identity,
 * result-file writing, and keep-screen-on. Classic bridge module (works on
 * new arch via interop layer).
 */
class BenchProbeModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "BenchProbe"

    // ---- Memory ----

    @ReactMethod
    fun getMemorySnapshot(promise: Promise) {
        try {
            val mi = Debug.MemoryInfo()
            Debug.getMemoryInfo(mi)
            val map = Arguments.createMap()
            map.putDouble("totalPssMb", mi.totalPss / 1024.0) // totalPss is KB
            map.putDouble("vmHwmMb", readVmHwmKb() / 1024.0)
            map.putDouble("timestampMs", System.currentTimeMillis().toDouble())
            promise.resolve(map)
        } catch (e: Exception) {
            promise.reject("E_MEM", e)
        }
    }

    private fun readVmHwmKb(): Double {
        return try {
            File("/proc/self/status").readLines()
                .firstOrNull { it.startsWith("VmHWM:") }
                ?.replace(Regex("[^0-9]"), "")?.toDoubleOrNull() ?: -1.0
        } catch (e: Exception) {
            -1.0
        }
    }

    // ---- Thermal + battery ----

    @ReactMethod
    fun getThermalSnapshot(promise: Promise) {
        try {
            val pm = reactContext.getSystemService(Context.POWER_SERVICE) as PowerManager
            val map = Arguments.createMap()

            if (Build.VERSION.SDK_INT >= 29) {
                map.putInt("thermalStatus", pm.currentThermalStatus)
            } else {
                map.putInt("thermalStatus", -1)
            }
            if (Build.VERSION.SDK_INT >= 30) {
                val headroom = pm.getThermalHeadroom(0)
                if (headroom.isNaN()) map.putNull("thermalHeadroom")
                else map.putDouble("thermalHeadroom", headroom.toDouble())
            } else {
                map.putNull("thermalHeadroom")
            }

            val batteryIntent: Intent? = reactContext.registerReceiver(
                null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)
            )
            if (batteryIntent != null) {
                val level = batteryIntent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                val scale = batteryIntent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
                val tempTenths = batteryIntent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1)
                val status = batteryIntent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
                map.putDouble(
                    "batteryPct",
                    if (level >= 0 && scale > 0) level * 100.0 / scale else -1.0
                )
                if (tempTenths > 0) map.putDouble("batteryTempC", tempTenths / 10.0)
                else map.putNull("batteryTempC")
                map.putBoolean(
                    "charging",
                    status == BatteryManager.BATTERY_STATUS_CHARGING ||
                        status == BatteryManager.BATTERY_STATUS_FULL
                )
            } else {
                map.putDouble("batteryPct", -1.0)
                map.putNull("batteryTempC")
                map.putBoolean("charging", false)
            }
            map.putBoolean("powerSaveMode", pm.isPowerSaveMode)
            map.putDouble("timestampMs", System.currentTimeMillis().toDouble())
            promise.resolve(map)
        } catch (e: Exception) {
            promise.reject("E_THERMAL", e)
        }
    }

    // ---- Device identity ----

    @ReactMethod
    fun getDeviceInfo(promise: Promise) {
        try {
            val am = reactContext.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            val memInfo = ActivityManager.MemoryInfo()
            am.getMemoryInfo(memInfo)
            val map = Arguments.createMap()
            map.putString("manufacturer", Build.MANUFACTURER)
            map.putString("model", Build.MODEL)
            map.putString("device", Build.DEVICE)
            map.putString(
                "soc",
                if (Build.VERSION.SDK_INT >= 31) "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}"
                else Build.HARDWARE
            )
            map.putInt("androidSdk", Build.VERSION.SDK_INT)
            map.putString("androidRelease", Build.VERSION.RELEASE)
            map.putInt("cpuCores", Runtime.getRuntime().availableProcessors())
            map.putDouble("totalMemMb", memInfo.totalMem / (1024.0 * 1024.0))
            promise.resolve(map)
        } catch (e: Exception) {
            promise.reject("E_DEVINFO", e)
        }
    }

    // ---- Result export ----

    /** Appends a line to a file under the app's external files dir (adb-pullable without root). */
    @ReactMethod
    fun appendResultLine(fileName: String, line: String, promise: Promise) {
        try {
            val dir = File(reactContext.getExternalFilesDir(null), "results")
            if (!dir.exists()) dir.mkdirs()
            val f = File(dir, fileName)
            f.appendText(line + "\n")
            promise.resolve(f.absolutePath)
        } catch (e: Exception) {
            promise.reject("E_WRITE", e)
        }
    }

    // ---- Run hygiene ----

    @ReactMethod
    fun setKeepScreenOn(on: Boolean, promise: Promise) {
        val activity = currentActivity
        if (activity == null) {
            promise.reject("E_NO_ACTIVITY", "No current activity")
            return
        }
        UiThreadUtil.runOnUiThread {
            if (on) activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            else activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            promise.resolve(null)
        }
    }

    /** Hint a GC between cold runs (best-effort; documented as such). */
    @ReactMethod
    fun requestGc(promise: Promise) {
        System.gc()
        Runtime.getRuntime().gc()
        promise.resolve(null)
    }

    @ReactMethod
    fun getFileSizeMb(path: String, promise: Promise) {
        try {
            val f = File(path.removePrefix("file://"))
            promise.resolve(if (f.exists()) f.length() / (1024.0 * 1024.0) else -1.0)
        } catch (e: Exception) {
            promise.reject("E_STAT", e)
        }
    }
}
