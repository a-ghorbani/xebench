package com.xebenchapp

import com.facebook.react.ReactPackage

/** Experimental flavor: LiteRT-LM has no autolinked RN binding, so register it here. */
internal fun flavorPackages(): List<ReactPackage> = listOf(LiteRtBenchPackage())
