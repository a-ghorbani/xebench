# Run conditions

Policy `run-conditions-v2` (issue #2) and `charging-policy-v1`. Both policies are
copied into every session record. They define when a repetition may start, what is
observed while it runs, and when a condition verdict may pass. They do not verify
backend execution, quality or cross-device comparability.

## Per-repetition sequence

1. **Cooldown:** a fixed `cooldownSec` sleep before every repetition except the
   first of a configuration invocation, including the first repetition of later runs.
2. **Readiness:** native snapshots are polled every 5 s until ready, for at most
   `readinessTimeoutSec` (default 180 s, configurable from 10 to 900 s). The final
   ready snapshot is the `before` endpoint. On timeout the repetition is recorded
   as `skipped` (`readiness-timeout`), nothing is loaded, and the session stops.
3. **Sampling:** a native background thread samples once per second from just
   before `load()` until `benchOnce()` returns. No JS-bridge sampling call happens
   between the start of load and the end of inference.
4. **After endpoint:** sampling stops, then the `after` snapshot (with PSS) is taken
   before the engine is released, and the checkpoint is saved.

## Readiness (wait, then skip)

Ready requires a complete probe, every required signal measured, no run-time failure
(below), and:

- Android thermal status `NONE` (0);
- battery temperature at most 40 °C, and at most 2 °C above the first ready reading
  of the configuration invocation (the baseline is shared across its runs);
- thermal headroom at most 0.8 when the API returns a value. Headroom is null when
  unsupported (API < 30), rate-limited or NaN; null is not used to pass or fail.

Required signals: battery %, battery temperature, power saving, thermal status,
screen interactive, foreground window focus, keyguard lock and the focus-loss count.
Unknown required evidence is never ready, so an unavailable probe always times out.

## Run-time observation (record and flag)

Endpoints and every sample are checked for battery below 50%, thermal status
`SEVERE` (3) or worse, power saving, screen off, lost foreground focus and lock.
A native focus-loss counter, incremented by the activity on the UI thread, also
flags an `interrupted` repetition when focus was lost between the endpoints,
including brief interruptions between samples. Flags keep the measurements.

Sampling reads battery, power, thermal status/headroom, screen, lock, focus and
`VmRSS`; it skips PSS because its page walk is comparatively expensive. Each
record stores the native sampling cost (`maxSampleCostMs`, `totalSampleCostMs`),
a worst-case summary over all samples, and at most 30 evenly spaced samples.
Monitors expire after 15 minutes. Expiry, dropped or incomplete samples, or a
missing monitor leave the repetition `unverified`. RSS includes shared mapped
model pages; neither sampled RSS nor endpoint PSS is peak model memory.

## Verdict and guards

`conditionVerdict.status` is:

- `fail` when any readiness timeout, pending probe, endpoint or sampled failure, or
  interruption was observed (`guardsPassed=false`; reasons copied to `guardNotes`);
- `pass` only when every planned repetition completed, was ready, and every
  endpoint and sampling assessment passed with measured evidence;
- `unverified` otherwise, including failed engine repetitions and incomplete coverage.

`guardsPassed` is never `true`: actual backend execution is still unverified, so a
condition pass is necessary but not sufficient.

## Charging policy (owner decision)

`charging-policy-v1` requires external power to be **disconnected** for publication
eligibility. This is a conservative default chosen pending the owner's decision. It
avoids charging heat and charger/OEM power-path differences. The `charging`
field means external power is connected, including a full battery on USB.
`conditionVerdict.charging` is `satisfied` only when every endpoint and sample
measured disconnected power; any connection is `violated`; otherwise `unknown`. It
does not affect run validity. USB-tethered captures are therefore `violated`;
eligible runs need, for example, wireless debugging.

## Failures, skipped runs and cleanup

- Load, inference and validation failures release the engine, save the attempt and
  stop the session (`stopReason: repetition-failed`).
- A probe or monitor call that times out, is busy, gets stuck or rejects while
  stopping saves a failed attempt, stops sampling where possible, releases the
  engine and aborts the configuration (`condition-probe-pending`). Native sampling
  is single-flight, and a stuck monitor stays registered so later starts report
  busy instead of overlapping.
- Release failure saves both errors and aborts the configuration (`cleanup-failed`).
- After an abort, each remaining run is saved as a failed session with
  `configuration-aborted`; an unreadable model yields `setup-failed` (artifact
  `null`), and later runs continue.

## Instance, process and order identity

Each measured repetition is the first request on a freshly loaded instance
(`instance.kind: fresh-instance`) in a warm process. `processInstanceId` is random
per JS process. `modelLoadsBeforeInProcess` and native `processUptimeMs` separate
process restarts from reuse. Warm requests and per-repetition restarts are not
measured (`protocol.warmRequests: not-measured`). Runs execute in a seeded
Fisher-Yates order (mulberry32); `runOrder` records the seed, its source
(`orderSeed` in the configuration or generated), the order and the run's position.
Repetitions of one run stay contiguous.

## Rule traceability

| Rule | Implementation | Validation |
| --- | --- | --- |
| Unknown required evidence never passes | [`conditions.ts`](../xebenchapp/src/harness/conditions.ts) `assessConditions`, `assessReadiness`, `assessDuring` | [`conditions.test.ts`](../xebenchapp/__tests__/conditions.test.ts) invalid/missing readings, missing module; [`session.test.ts`](../xebenchapp/__tests__/session.test.ts) unavailable probes, missing monitor |
| Bounded readiness wait; timeout skips | [`readiness.ts`](../xebenchapp/src/harness/readiness.ts), [`session.ts`](../xebenchapp/src/harness/session.ts) | `conditions.test.ts` bounded readiness wait; `session.test.ts` readiness timeout, shared baseline |
| Cooldown before later repetitions/runs | `session.ts` `runSession`, [`reference.ts`](../xebenchapp/src/harness/reference.ts) | `session.test.ts` checkpoints before cooldown, initial cooldown; [`reference.test.ts`](../xebenchapp/__tests__/reference.test.ts) later-run cooldown |
| Sampling outside JS during timed work; bounded and expiring | [`BenchConditionsModule.kt`](../xebenchapp/android/app/src/main/java/com/xebenchapp/BenchConditionsModule.kt) `startMonitor`/`stopMonitor`; `conditions.ts` `normalizeMonitor` | `session.test.ts` event order, incomplete coverage; `conditions.test.ts` bounded samples, coverage; device runs |
| Interruption detection | [`MainActivity.kt`](../xebenchapp/android/app/src/main/java/com/xebenchapp/MainActivity.kt) focus counter; `assessDuring` | `conditions.test.ts` focus losses; `session.test.ts` interruption then readiness wait; device runs |
| Unresponsive probes/monitors abort after save and release | `conditions.ts` `bounded`; `session.ts` | `conditions.test.ts` unresponsive calls; `session.test.ts` late probes, busy sampler, monitor start/stop failures |
| Cleanup on failure; unmeasured runs recorded | `session.ts`, `reference.ts` | `session.test.ts` failed load/inference/release, monitor stop on failure; `reference.test.ts` cleanup abort, unreadable model |
| Charging policy affects eligibility only | `conditions.ts` `assessCharging`; `session.ts` `conditionVerdict` | `conditions.test.ts` charging policy; `session.test.ts` identity/charging |
| Instance/process identity, seeded run order | `session.ts`; [`order.ts`](../xebenchapp/src/harness/order.ts) | `session.test.ts` identity; [`order.test.ts`](../xebenchapp/__tests__/order.test.ts); `reference.test.ts` run order |

## Limits

Thresholds are protocol choices, not device-calibrated limits. Battery temperature
is a proxy for device temperature. Headroom support and scale vary by device. A
1 Hz sampler can miss sub-second thermal or battery changes; only focus losses
are counted between samples. Device validation covers the devices named in the
pull request, not other models. Publication remains blocked (#4).

API semantics: [thermal status](https://developer.android.com/reference/android/os/PowerManager#getCurrentThermalStatus()),
[thermal headroom](https://developer.android.com/reference/android/os/PowerManager#getThermalHeadroom(int)),
[process PSS](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss()).
