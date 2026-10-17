package com.xebenchapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MonitorScheduleTest {
  private val interval = 1000L

  /** Models the monitor loop: an immediate first sample, then one sample per wake-up. */
  private fun simulate(durationMs: Long, costMs: Long, oversleepMs: Long, fixedRate: Boolean): List<Long> {
    val ticks = mutableListOf<Long>()
    var now = 0L
    while (now <= durationMs) {
      ticks += now
      now += costMs
      val delay = if (fixedRate) MonitorSchedule.nextDelayMs(0, now, interval) else interval
      now += delay + oversleepMs
    }
    return ticks
  }

  /** Mirrors the coverage rule (conditions.ts sampledThroughout / raw_session.py). */
  private fun covered(ticks: List<Long>, durationMs: Long): Boolean {
    val times = listOf(0L) + ticks + listOf(durationMs)
    val maxGap = times.zipWithNext { a, b -> b - a }.maxOrNull() ?: 0
    return ticks.size >= durationMs / interval && maxGap <= 2 * interval
  }

  @Test
  fun nextSlotIsOnTheGrid() {
    assertEquals(1000L, MonitorSchedule.nextDelayMs(0, 0, interval))
    assertEquals(986L, MonitorSchedule.nextDelayMs(0, 14, interval))
    assertEquals(500L, MonitorSchedule.nextDelayMs(0, 4500, interval))
    // A tick late by several intervals skips the missed slots instead of bursting.
    assertEquals(200L, MonitorSchedule.nextDelayMs(0, 7800, interval))
  }

  @Test
  fun longRunsStayCoveredWithRealisticCost() {
    // The OnePlus 6 measured about 14 ms per sample; add 2 ms oversleep per wake-up.
    for (duration in listOf(43_200L, 120_000L, 300_000L, 900_000L)) {
      assertTrue("fixed rate covers $duration ms", covered(simulate(duration, 14, 2, true), duration))
    }
  }

  @Test
  fun fixedDelayDriftsOutOfCoverage() {
    // The previous loop (a full interval after each sample) loses about one tick per minute.
    assertTrue(covered(simulate(43_200L, 14, 0, false), 43_200L))
    assertFalse(covered(simulate(300_000L, 14, 2, false), 300_000L))
  }
}
