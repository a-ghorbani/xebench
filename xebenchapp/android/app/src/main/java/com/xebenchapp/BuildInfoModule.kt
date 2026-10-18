package com.xebenchapp

import android.content.pm.PackageManager
import android.content.pm.Signature
import android.os.Build
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

/**
 * Identity of the installed binary for session records (issue #3). The commit, dirty
 * flag and bundled-configuration hash are BuildConfig fields set by app/build.gradle;
 * the signing certificates are read from the installed package. Unknown values are
 * null, never guessed.
 */
class BuildInfoModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName() = "BuildInfo"

  private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

  @Suppress("DEPRECATION")
  private fun signers(): List<Signature> {
    val pm = context.packageManager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
      val info = pm.getPackageInfo(context.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
      return info.signingInfo?.apkContentsSigners?.toList() ?: emptyList()
    }
    return pm.getPackageInfo(context.packageName, PackageManager.GET_SIGNATURES).signatures?.toList() ?: emptyList()
  }

  /**
   * GNU build ID (NT_GNU_BUILD_ID note) of a 64-bit little-endian ELF file, or null.
   * Reads only the program headers and PT_NOTE segments; never throws.
   */
  private fun elfBuildId(file: File): String? = runCatching {
    RandomAccessFile(file, "r").use { raf ->
      val header = ByteArray(64).also { raf.readFully(it) }
      val h = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
      if (h.getInt(0) != 0x464c457f || header[4].toInt() != 2 || header[5].toInt() != 1) return@use null
      val phoff = h.getLong(32)
      val phentsize = h.getShort(54).toInt() and 0xffff
      val phnum = h.getShort(56).toInt() and 0xffff
      if (phentsize < 56 || phnum > 64) return@use null
      for (i in 0 until phnum) {
        val ph = ByteArray(56).also { raf.seek(phoff + i.toLong() * phentsize); raf.readFully(it) }
        val p = ByteBuffer.wrap(ph).order(ByteOrder.LITTLE_ENDIAN)
        if (p.getInt(0) != 4) continue // PT_NOTE
        val offset = p.getLong(8)
        val size = p.getLong(32)
        if (size <= 0 || size > 65536) continue
        val notes = ByteArray(size.toInt()).also { raf.seek(offset); raf.readFully(it) }
        val n = ByteBuffer.wrap(notes).order(ByteOrder.LITTLE_ENDIAN)
        var at = 0
        while (at + 12 <= notes.size) {
          val namesz = n.getInt(at)
          val descsz = n.getInt(at + 4)
          val type = n.getInt(at + 8)
          val nameAt = at + 12
          val descAt = nameAt + ((namesz + 3) and 3.inv())
          if (namesz < 0 || descsz < 0 || descAt + descsz > notes.size) break
          val name = String(notes, nameAt, maxOf(0, namesz - 1), Charsets.US_ASCII)
          if (type == 3 && name == "GNU") return@use hex(notes.copyOfRange(descAt, descAt + descsz))
          at = descAt + ((descsz + 3) and 3.inv())
        }
      }
      null
    }
  }.getOrNull()

  /**
   * The llama.rn native libraries mapped in this process (/proc/self/maps) with their ELF
   * build IDs, so a session record names the exact binary that ran. Empty before llama.rn
   * loads its library; rejected (unknown) if the maps cannot be read.
   */
  @ReactMethod
  fun nativeLibraries(promise: Promise) {
    try {
      val paths = File("/proc/self/maps").readLines()
        .map { it.substringAfterLast(' ') }
        .filter { it.startsWith("/") && it.substringAfterLast('/').let { name -> name.startsWith("librnllama") && name.endsWith(".so") } }
        .distinct().sorted()
      val list = Arguments.createArray()
      for (path in paths) {
        val entry = Arguments.createMap()
        entry.putString("name", path.substringAfterLast('/'))
        val id = elfBuildId(File(path))
        if (id == null) entry.putNull("buildId") else entry.putString("buildId", id)
        list.pushMap(entry)
      }
      promise.resolve(list)
    } catch (error: Exception) {
      promise.reject("native_libraries", error)
    }
  }

  @ReactMethod
  fun identity(promise: Promise) {
    try {
      val map = Arguments.createMap()
      if (BuildConfig.XEBENCH_GIT_COMMIT.isEmpty()) map.putNull("gitCommit")
      else map.putString("gitCommit", BuildConfig.XEBENCH_GIT_COMMIT)
      when (BuildConfig.XEBENCH_GIT_DIRTY) {
        "true" -> map.putBoolean("gitDirty", true)
        "false" -> map.putBoolean("gitDirty", false)
        else -> map.putNull("gitDirty")
      }
      map.putString("flavor", BuildConfig.FLAVOR)
      map.putString("buildType", BuildConfig.BUILD_TYPE)
      map.putString("versionName", BuildConfig.VERSION_NAME)
      map.putInt("versionCode", BuildConfig.VERSION_CODE)
      if (BuildConfig.XEBENCH_BUNDLED_CONFIG_SHA256.isEmpty()) map.putNull("bundledConfigSha256")
      else map.putString("bundledConfigSha256", BuildConfig.XEBENCH_BUNDLED_CONFIG_SHA256)
      val certs = runCatching {
        signers().map { hex(MessageDigest.getInstance("SHA-256").digest(it.toByteArray())) }
      }.getOrNull()
      if (certs.isNullOrEmpty()) map.putNull("signingCertSha256")
      else map.putArray("signingCertSha256", Arguments.fromList(certs))
      promise.resolve(map)
    } catch (error: Exception) {
      promise.reject("build_info", error)
    }
  }
}
