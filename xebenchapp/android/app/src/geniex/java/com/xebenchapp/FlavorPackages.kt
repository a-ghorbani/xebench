package com.xebenchapp

import com.facebook.react.ReactPackage

/** GENIE-X flavor: geniex-android has no RN binding, so register it here. */
internal fun flavorPackages(): List<ReactPackage> = listOf(GenieXBenchPackage())
