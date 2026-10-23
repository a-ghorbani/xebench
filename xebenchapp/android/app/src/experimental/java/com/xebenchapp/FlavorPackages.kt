package com.xebenchapp

import com.facebook.react.ReactPackage

/**
 * Experimental flavor: LiteRT-LM has no autolinked RN binding, so register it here, and
 * ExecutorchBench, which marks this build as the one that packages ExecuTorch.
 */
internal fun flavorPackages(): List<ReactPackage> = listOf(LiteRtBenchPackage(), ExecutorchBenchPackage())
