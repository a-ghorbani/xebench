package com.xebenchapp

import android.content.pm.PackageManager
import android.content.pm.Signature
import android.os.Build
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
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
