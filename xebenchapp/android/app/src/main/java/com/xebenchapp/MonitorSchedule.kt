package com.xebenchapp

/**
 * Fixed-rate schedule for the condition monitor: samples are due at start + k * interval.
 * Unlike sleeping a fixed interval after each sample, sampling cost and oversleep do not
 * accumulate, so the tick count keeps up with elapsed time on long runs. A late tick (e.g. a
 * frozen process) skips the missed slots instead of bursting; the gap stays visible to the
 * coverage check, which then reports the run as unverified.
 */
object MonitorSchedule {
  /** Delay from [nowMs] until the next slot strictly after [nowMs]. */
  fun nextDelayMs(startMs: Long, nowMs: Long, intervalMs: Long): Long {
    require(intervalMs > 0) { "interval must be positive" }
    val elapsed = (nowMs - startMs).coerceAtLeast(0)
    val nextSlot = startMs + (elapsed / intervalMs + 1) * intervalMs
    return nextSlot - nowMs
  }
}
