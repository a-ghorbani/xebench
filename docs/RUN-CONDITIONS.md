# Run conditions

Policy `run-conditions-v2` (issue #2) and `charging-policy-v3`. Both policies are
copied into every session record. They define when a repetition may start, what is
observed while it runs, and when a condition verdict may pass. They do not verify
backend execution, quality or cross-device comparability.

## Per-repetition sequence

1. **Cooldown:** a fixed `cooldownSec` sleep before every repetition, including the
   first repetition of each run (after its model has been hashed).
2. **Readiness:** native snapshots are polled every 5 s until ready, for at most
   `readinessTimeoutSec` (default 180 s, configurable from 10 to 900 s). The final
   ready snapshot is the `before` endpoint. On timeout the repetition is recorded
   as `skipped` (reason `readiness-timeout`, or `charging` when the battery was still
   charging), nothing is loaded, and the session stops.
3. **Sampling:** a native background thread samples immediately and then once per
   second from just before `load()` until `benchOnce()` returns. No JS-bridge
   sampling call happens between the start of load and the end of inference.
4. **After endpoint:** sampling stops, then the `after` snapshot (with PSS) is taken
   before the engine is released, and the checkpoint is saved.

## Readiness (wait, then skip)

Ready requires a complete probe, every required signal measured, no run-time failure
(below), and:

- Android thermal status `NONE` (0);
- battery temperature at most 40 °C, and at most `maxBatteryTempRiseC` (default
  2 °C; configurable from 0.5 to 10) above the first ready reading of the
  configuration invocation. The baseline is shared across that invocation's runs.
  The effective bound is recorded in `configuration` and `protocol`, like
  `readinessTimeoutSec`. Follow-up: the charging A/B experiment will measure
  battery-temperature behaviour, and the final default will be set from that data;
- thermal headroom at most 0.8 when the API returns a value. Headroom is null when
  unsupported (API < 30), rate-limited or NaN (the first call can be NaN). An
  otherwise-ready poll with null headroom is repeated once after 1.5 s; if headroom
  is still null it is not used, and `readiness.headroomApplied` records `false`;
- the battery not actively charging (see the charging policy). An unknown charging
  state does not block readiness, but it is never eligible.

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
Coverage passes only with at least one sample, no gap above two intervals between
monitor start, samples and stop, and at least one tick per elapsed interval.
Monitors expire after 15 minutes. Expiry, gaps, dropped or incomplete samples, or a
missing monitor leave the repetition `unverified`. `pssMb` and `rssMb` are MiB.
RSS includes shared mapped model pages; neither sampled RSS nor endpoint PSS is
peak model memory.

## Verdict and guards

`conditionVerdict.status` is:

- `fail` when any readiness timeout with an observed not-ready condition, pending
  probe or monitor, endpoint or sampled failure, or interruption was observed
  (`guardsPassed=false`; reasons copied to `guardNotes`);
- `pass` only when every planned repetition completed, was ready, and every
  endpoint and sampling assessment passed with measured evidence;
- `unverified` otherwise, including failed engine repetitions, incomplete coverage
  and readiness timeouts caused only by unknown evidence.

`guardsPassed` is never `true`: actual backend execution is still unverified, so a
condition pass is necessary but not sufficient.

## Charging policy (owner decision)

`charging-policy-v3` ("tethered, not charging") follows the owner's decision. Lab
phones stay USB-tethered, so **external power is allowed** but **active charging
is not**. The policy only affects publication eligibility. It never removes
measurements or changes run validity.

Each snapshot and 1 Hz sample records three raw values:

- `batteryStatus` (`BatteryManager` `EXTRA_STATUS`);
- `plugged` (`EXTRA_PLUGGED`);
- `currentNowUa` (`BATTERY_PROPERTY_CURRENT_NOW`).

Each observation is classified by the battery status alone:

- **not charging**: status `FULL` (5) or `NOT_CHARGING` (4), for example when a
  charge limit is reached;
- **charging**: status `CHARGING` (2);
- **on battery**: status `DISCHARGING` (3). This is a known state, but the tethered
  lab policy does not admit it. A future policy version may admit on-battery runs
  for external operators (#7), with power source as a comparison dimension;
- **unknown**: an unknown or unreadable status.

`currentNowUa` is recorded raw as a diagnostic and never decides eligibility.
Its units and sign are vendor-defined: the S23's values look like mA, not the
documented µA. `FULL` means no charging heat, not zero current. Under load, the S23
discharged at roughly 1–2 A while it reported `FULL` on USB.

A repetition's `conditions.charging` and the session's `conditionVerdict.charging`
take one of four values:

- `violated` if any endpoint or sample was charging;
- `on-battery` otherwise, if any was on battery (not eligible under this policy);
- `satisfied` only when every observation was measured as not charging, and every
  measured repetition was sampled;
- `unknown` otherwise. Unknown is never eligible.

`charging-policy-v2`, written by earlier builds of this work, applies the same
rules but counts `DISCHARGING` as unknown. The validator still accepts it, with
those semantics.

Readiness also waits, within `readinessTimeoutSec`, while the battery is charging.
On timeout the repetition is skipped with reason `charging`. Because charging is
not a validity condition, that session's verdict is `unverified`, not `fail`. The
harness never fakes an unplug (for example with `dumpsys battery unplug`), since
that changes the reported state, not the physics.

Lab setup: enable the device's charge limit where available. Examples are Pixel
"Charging optimization" (limit to 80%) and Samsung "Battery protection". A
device without a charge limit can only produce `violated` (charging) or `unknown`
runs while tethered. Those runs are labelled as such, which is acceptable. During
the validation runs, the Galaxy S23 reported `FULL` with battery protection
active. The Pixel 9 was on AC power below 80%.

## Failures, skipped runs and cleanup

- Load, inference and validation failures release the engine, save the attempt and
  stop the session (`stopReason: repetition-failed`).
- A probe or monitor call that times out, is busy, gets stuck or rejects while
  stopping saves a failed attempt, stops sampling where possible, releases the
  engine and aborts the configuration (`condition-probe-pending`). Native probes
  and the monitor exclude each other; a stuck monitor stays registered so later
  probes and starts report busy instead of overlapping. Each invocation first stops
  any monitor left by an earlier one. Empty or failed native results are
  `unavailable`, never complete.
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
| Charging policy: tethered allowed, charging never eligible, unknown never eligible, wait while charging | `conditions.ts` `chargingState`, `assessCharging`, `assessReadiness`; `session.ts` `chargingCounts`, `conditionVerdict`; `BenchConditionsModule.kt` raw status/plugged/current | `conditions.test.ts` charging-policy-v3; `session.test.ts` charging-policy-v3 in sessions; `test_raw_session.py` charging recompute |
| Instance/process identity, seeded run order | `session.ts`; [`order.ts`](../xebenchapp/src/harness/order.ts) | `session.test.ts` identity; [`order.test.ts`](../xebenchapp/__tests__/order.test.ts); `reference.test.ts` run order |
| Records cannot claim what their evidence does not support | [`raw_session.py`](../scripts/raw_session.py) `_semantic_v2` recomputes assessments, readiness, verdict and order | [`test_raw_session.py`](../tests/test_raw_session.py) `ConditionsV2Tests`; [`rawSessionSchema.test.ts`](../xebenchapp/__tests__/rawSessionSchema.test.ts) drift fixture |
| v2 evidence survives sanitized export | [`export_evidence.py`](../scripts/export_evidence.py) allowlist (snapshot, monitor, readiness, charging, verdict, order) | [`test_export_evidence.py`](../tests/test_export_evidence.py) `ConditionsV2ExportTests` (capture → validation → export → verify) |

Native rules (monitor timing and expiry, single-flight, focus counter) have no
JVM unit tests. Their device validation is listed in the pull request that
introduced this policy (#2 work): Pixel 9 and Galaxy S23 runs covering readiness
waits and timeouts, sampling, interruption detection and recomputed record
validation.

## Device observations (two devices, not general limits)

On a Pixel 9 (AC power) and a Galaxy S23 (USB power), idle thermal status was 0
and headroom about 0.44–0.63. One sample cost about 2–5 ms; the first sample was
up to about 54 ms. Battery temperature rose about 2–3 °C over five or six
repetitions and did not fall back within the 180 s readiness bound. The 2 °C
rise rule therefore delayed (up to 101 s) and then skipped a repetition of the
second run of a two-run, three-repetition configuration on both devices
(`battery-not-cooled`). This is the intended
behavior, but it limits configuration length on tethered devices. The owner kept the
2 °C default. The bound is now configurable per configuration, and the charging
A/B experiment will set the final value.

With the status-only charging policy, the S23 reported `FULL` on USB throughout a measured
three-repetition run (`satisfied`). Its `current_now` values (about −900 to −2000
during inference, near 0 at rest) look like mA rather than the documented µA. They
show the battery discharging at roughly 1–2 A under load while it reported `FULL`.
Because units and sign are vendor-defined, current never decides eligibility.

A OnePlus 6 (Android 11) throttles under load. In one capture, it still reported
`SEVERE` shortly after earlier use, and readiness refused to start. In the
final-code capture, it was at thermal status 0 at idle. Readiness waited 25 s for
headroom, and the device then reached `SEVERE` during the first repetition (tg
9.1 tok/s, TTFT 26.5 s, flagged `severe-thermal-state`, metrics kept). The second
repetition was skipped after a readiness timeout. This is real throttling. The
device stays in the lab as an example of an old device that throttles, not as a
reference device. It reported `currentNowUa` 0 at `FULL`.

## Limits

Thresholds are protocol choices, not device-calibrated limits. Battery temperature
is a proxy for device temperature. Headroom support and scale vary by device. A
1 Hz sampler can miss sub-second thermal or battery changes; only focus losses
are counted between samples. Device validation covers the devices named in the
pull request, not other models. Publication remains blocked (#4).

API semantics: [thermal status](https://developer.android.com/reference/android/os/PowerManager#getCurrentThermalStatus()),
[thermal headroom](https://developer.android.com/reference/android/os/PowerManager#getThermalHeadroom(int)),
[process PSS](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss()).
