# Experiment: charging vs not charging on a tethered phone

Status: **pre-registered**. This plan was written and committed before any
device run. Results are added below it later. The plan is not edited after
data collection starts. Any deviation is listed under "Deviations".

Part of #2 (run conditions) and #5.

## Question

Lab phones stay USB-tethered all the time. Historical xebench rows were measured
tethered, and their charging state is unknown. `charging-policy-v2`
("tethered-not-charging") makes a repetition publication-eligible only when
Android reports `FULL` or `NOT_CHARGING` at every snapshot and every 1 Hz sample.

The experiment asks how much "plugged and actively charging" (A) changes the
measurements compared with "plugged and not charging" (B). The owner uses the
answer to decide if legacy rows stay comparable with new eligible rows.

Being plugged in does not mean net charging. On a PC USB port the Galaxy S23
reported `FULL` while it discharged at about 1–2 A under LLM load. The condition
label is therefore the Android `batteryStatus` value, not the current.
`currentNowUa` is a vendor-defined raw value (the S23 value looks like mA) and is
a diagnostic only.

## Conditions

Both conditions use the same device, model, configuration and USB cable/port. The
phone is never unplugged, and the unplug is never faked: `dumpsys battery
unplug`/`set` are not used.

| | Label rule (every endpoint and persisted sample) | How the state is reached |
| --- | --- | --- |
| **A** plugged, charging | `batteryStatus` = `CHARGING` (2) and `plugged` ≠ 0 | Charge limit / battery protection temporarily **off**, battery below 100% |
| **B** plugged, not charging | `batteryStatus` ∈ {`NOT_CHARGING` (4), `FULL` (5)} and `plugged` ≠ 0 | Charge limit **on** and reached, or battery at 100% |

A repetition that does not meet one rule completely is **excluded** from the
primary comparison and counted by reason: mixed statuses, `DISCHARGING` (3),
unknown status or plug, or no complete sampling. Examples are a B block that
resumes charging below the limit, or an A block that reaches 100%. The planned
condition of each block is also recorded. The number of repetitions whose
observed label differs from the planned one is reported.

The monitor persists at most 30 evenly spaced samples. A repetition of this
model takes about 10 s, so every sample is persisted. If more than 30 samples
are taken, the summary's charging-state counts cover the samples that are not
persisted. In that case, a `DISCHARGING` sample that is not persisted cannot be
told apart from `FULL`/`NOT_CHARGING`.

### Device settings (owner-approved, restored afterwards)

The setting that switches between A and B depends on the device. During the
lease it is first found with read-only commands (`settings list
global|system|secure`, filtered for protect/battery/charge keys, and `dumpsys
battery`). It is changed with `adb shell settings put` when such a key exists.
UI automation (`input tap`) is used only when no key exists. Each change is
listed in the lease request. The original value is recorded before the first
change. The value is restored in a `finally` block and checked by reading it
again (`PHONE_DONE` reports both values).

| Device | Setting (candidate) | A | B |
| --- | --- | --- | --- |
| Galaxy S23 | Samsung "Battery protection" | off | on (limit reached) |
| POCO F8 Ultra (HyperOS) | HyperOS battery protection / charge limit, if present | off | on (limit reached) |
| Pixel 9 (only if available) | "Charging optimization → Limit to 80%" | off (adaptive/none) | limit to 80% (reached) |

**Fallback (pre-registered):** some devices have no usable charge limit. On those,
the battery can only charge to 100% (`FULL`) and drain under load until it
recharges. The device then runs the same blocks without a setting change. Each
repetition is labelled by its observed status ("observational mode"). These
results are reported separately as weaker evidence, because the state is not
assigned by design.

OnePlus 6 is excluded: it reports a stuck `SEVERE` thermal status.

## Build and protocol

- Source: branch `feat/conditions-enforcement` at `71c8d79` (charging-policy-v2,
  run-conditions-v2), plus one experiment-only commit on `exp/charging-ab`. The
  exact build commit and APK SHA-256 are given in "Build identity".
- Configuration: [`experiment-charging-ab-v1.json`](../../xebenchapp/configs/experiment-charging-ab-v1.json).
  It uses the reference CPU run of `cpu-reference-v1`: Llama-3.2-1B-Instruct
  Q4_K_M, llama.cpp CPU, 4 threads, n_ctx 2048, 128 decode tokens, 30 s
  cooldown, 180 s readiness bound. The only addition is
  `experimentChargingReadiness: "record-only"`.
- **Experiment-only flag:** `experimentChargingReadiness: "record-only"`.
  Readiness still waits for thermal status `NONE`, battery temperature (≤ 40 °C
  and ≤ baseline + 2 °C), headroom and the other rules, but it does not wait for
  charging to stop. Without the flag, every A repetition would be skipped
  (`charging`). Charging is still recorded at every endpoint and sample. Such
  records carry `chargingPolicy.readiness: "record-only-experiment"`. The
  validator ([`raw_session.py`](../../scripts/raw_session.py)) accepts this value
  only with the flag, and recomputes readiness under it. These records are
  never publication evidence. Their A repetitions are `violated` under the
  charging policy anyway.
- **Blocks:** one block is one app invocation with 3 measured repetitions. Each
  repetition has a cooldown and a readiness wait, and is the first request on a
  freshly loaded instance. The readiness temperature baseline is reset at each
  block. A readiness timeout ends a block early. This is recorded, and the next
  block follows.
- **Interleaving:** blocks alternate A, B, A, B, … to reduce time and thermal
  drift. Counterbalancing across devices: the S23 starts with A, the POCO with
  B, and the Pixel with A.
- **State change between blocks:** the driver applies the setting, then polls
  `dumpsys battery` (read-only) every 10 s. The target status must appear on 3
  consecutive polls before the block starts. The wait is at most 10 min. A
  `KEYCODE_WAKEUP` key event is sent on each poll, so the screen does not time
  out. The block's own 30 s cooldown and readiness wait follow.
- Driver: [`charging_ab_run.py`](../../scripts/experiments/charging_ab_run.py).
  Capture: `scripts/capture_app_run.py`. Raw evidence stays local under
  `results/charging-ab/<device>/` (gitignored). Serials are never written.
- Install: only `com.xebenchapp`, with `adb install -r` (no uninstall, no data
  clear). If an app configuration is already staged, it is backed up and put
  back afterwards. No reboots. No other packages are touched.

## Measurements

From each measured repetition (raw session record, schema `raw-session-v1`):

- **Primary:** decode tok/s and prefill tok/s (engine timings).
- **Secondary:** TTFT (ms) and model load time (ms).
- **Condition diagnostics:** battery temperature before, after and maximum
  sampled; temperature rise over the repetition; maximum thermal status; maximum
  thermal headroom (a higher value means less headroom; 1.0 means `SEVERE`);
  `batteryStatus` and `plugged` at every endpoint and sample; median raw
  `currentNowUa` (raw value only); battery %; and the readiness wait time.
- Per condition: skipped repetitions and their readiness reasons (for example
  `battery-not-cooled`), failed repetitions, and rejected records.

## Analysis (fixed in advance)

The script is [`charging_ab_analyze.py`](../../scripts/experiments/charging_ab_analyze.py)
(stdlib only). It uses only valid records with the experiment flag, and runs
once per device:

- **Effect:** Δ = (median_B − median_A) / median_A × 100%, for each metric.
- **95% CI:** percentile bootstrap. A and B are resampled independently with
  replacement, 10,000 times, with seed 20261017 (+ metric index). Percentiles
  use linear interpolation.
- **Variance:** the ratio var_B / var_A of the sample variances, with a bootstrap
  95% CI. The IQR as a % of the median is also reported for each condition.
- **Thermal effects:** the median difference B − A in absolute units (°C,
  status, headroom, s) with a bootstrap 95% CI, plus counts of the maximum
  thermal status. "Charging heats the device" is supported when the CI for the
  before or maximum battery temperature lies entirely below 0 for B − A (A
  hotter). The same rule applies to thermal status and headroom.
- **Practical-relevance threshold: |Δ median| < 3%.** For each metric:
  - **irrelevant**: the whole 95% CI lies inside (−3%, +3%);
  - **relevant**: the whole 95% CI lies outside [−3%, +3%];
  - **inconclusive**: anything else. Whether the CI excludes 0 is also reported.
- With fewer than 10 pure-label repetitions in either condition, the device
  result is reported as descriptive only (no verdict).

### Why 3%

Earlier local `cpu-reference-v1` sessions measured this model and configuration
on the same phones (Pixel 9: n = 15; Galaxy S23: n = 14). Their within-session
IQR was 0.9–2.5% of the median for decode and 1.0–3.8% for prefill. A charging
effect below 3% is therefore about the same size as the ordinary run-to-run
spread of one device. It would not change any device or engine comparison that
xebench reports. An effect of 3% or more is larger than that spread, and it
could reorder close rows.

The same threshold is applied to TTFT and load time, but these are secondary.
Load time had an IQR of 16–83% of the median in those sessions, so a 3%
equivalence verdict is not expected to be reachable for load time. It will
likely be "inconclusive", and it does not drive the recommendation.

Expected precision for the primary metrics, given that spread: with 15
repetitions per condition, the 95% CI half-width of Δ is about ±1–2% for decode
and about ±2–3% for prefill. An "irrelevant" verdict therefore needs the true
effect to be at most about 1–2%.

## Sample size, stopping rules and time budget

- Target: **15 pure-label repetitions per condition per device** (5 A/B block
  pairs of 3 repetitions). The minimum is 10.
- The driver runs block pairs until both conditions have at least 15 pure-label
  repetitions. The check happens only after a complete pair. The driver stops
  earlier at **8 pairs** (48 repetitions) or at the **time budget**. Stopping
  never depends on the effect estimates. The pure-label count is the only
  data-dependent rule.
- Time budget: **≤ 3 h of device time per phone.** The driver gets 170 min and
  does not start a block with less than 6 min left. Restoring the settings
  is inside the budget.
- **Stop a device early** in any of these cases:
  - a target state is not reached within 10 min on two consecutive attempts for
    the same condition;
  - the battery level is below 55% (the run policy flags below 50%);
  - the thermal status is `SEVERE` or worse;
  - two consecutive captures fail (app or capture failure);
  - the PM ends the lease.
- If a device ends with fewer than 10 repetitions in a condition, its result is
  descriptive only, and the reason is reported.

## Recommendation rule (fixed in advance)

- Every tested device "irrelevant" for **both** decode and prefill: legacy
  tethered rows with unknown charging state stay comparable within the 3%
  resolution. They are labelled "charging state unknown".
- Any device "relevant" for decode or prefill: legacy rows with unknown charging
  state are **not comparable** with charging-policy-v2 rows. They are flagged and
  re-measured, or kept out of rankings.
- Otherwise (inconclusive): no comparability claim at 3% resolution. Legacy rows
  are labelled "charging state unknown, effect not ruled out". They are not
  ranked against v2 rows where differences are below about 3% plus the
  observed CI.

The results cover only the tested phones, this model and this configuration.

## Known limitations (stated before running)

- `CHARGING` is a status, not a net current. On weak USB ports the phone can
  report `CHARGING` while it discharges under load. The experiment measures what
  the policy labels, which is what eligibility depends on.
- The readiness gate equalizes the starting thermal state. Charging heat may
  therefore show up as longer readiness waits or skipped repetitions rather than
  as slower measurements. Legacy rows had no readiness gate, only a cooldown.
  Both outcomes are reported.
- The battery level differs between conditions by design. The charge-limit
  method keeps the difference small (a few %), but battery level and the charge
  controller's mode cannot be separated from the charging state.
- The setting change itself (for example a vendor charging daemon changing mode)
  could have effects beyond charging.
- There are only 2–3 devices, one model and one configuration. The results do not
  generalise to GPU/NPU back ends or longer workloads.

## Build identity

**Superseded by Amendment 1.** The build below was never installed on a device.
The branch was moved onto the final #20 code (status-only `charging-policy-v3`)
before any data was collected. The new build identity is given in Amendment 1.

Recorded at build time, before any device run:

- Base: `origin/feat/conditions-enforcement` at
  `71c8d797ffcdb4765ef6af0a3e6b06b21ff9fe05`.
- APK build commit: `06d75f3963fae4405947638b39bf517f0870121b` on
  `exp/charging-ab` (the experiment-only flag). Later commits on the branch
  change only docs, tests and host scripts, not app code.
- Build: `npm ci` (install scripts enabled), then
  `./gradlew assembleRelease` with `reactNativeArchitectures=arm64-v8a`. The
  APK is signed with the debug key, like every reference build.
- APK `app-release.apk`: 52,832,435 bytes, SHA-256
  `6b6ac8f7737a7d05539dd44cafad2e97795054ee5fe70d3f81f54648ab28ca28`.
- `scripts/check_reference_apk.py`: the JS bundle and arm64 llama.rn runtime are
  packaged. The bundle contains the `record-only-experiment` policy.
- Tests at the build commit: Jest 7 suites, 117 tests, all passed; `tsc
  --noEmit` clean. Python `unittest discover -s tests`: 86 tests, all passed,
  after the analysis tests were added. ESLint reports one existing error that
  this change did not add (`__tests__/config.test.ts` `_omitted`).
- Installed APK identity on each device is recorded by the capture manifest
  (`appIdentity`: base APK SHA-256 before and after each block).

## Amendment 1 (2026-10-17, before any data)

No device had been leased and no experiment repetition existed when this
amendment was written. The plan above stays valid except where this amendment
changes it.

### A1.1 Base code: final #20, charging-policy-v3

The experiment now builds on `main` at `b90545b`, where PR #20 has been merged.
That version includes the #19 build flavors and the coverage fix `347bacd`: a
repetition with an unsampled stretch is never `satisfied`. With this change,
experiment records validate under the final schema and validator, and their
evidence bundles verify. That version uses **`charging-policy-v3`**:

- the charging decision uses only the status: `FULL`/`NOT_CHARGING` are
  eligible and `CHARGING` is violated;
- `DISCHARGING` is an explicit `on-battery` state, which is not eligible;
- an unknown state is never eligible;
- `currentNowUa` is diagnostic only.

The record-only flag was ported to v3. Records now carry the v3 policy with
`readiness: "record-only-experiment"`. The validator accepts that value only
together with `experimentChargingReadiness: "record-only"`, and only with the v3
classification. The evidence export keeps both values, so a bundle never hides
the experiment marker.

Effect on the label rules: none. A is still `CHARGING` at every observation, and
B is still `FULL`/`NOT_CHARGING` at every observation. v3 counts every sample by
its exact status, including samples that are not persisted (`notCharging` is
exactly {4, 5}, and `DISCHARGING` is counted separately). The earlier caveat no
longer applies: an unpersisted `DISCHARGING` sample can now always be told apart.

### A1.2 Readiness overrides for this experiment

The experiment configuration
([`experiment-charging-ab-v1.json`](../../xebenchapp/configs/experiment-charging-ab-v1.json))
now sets:

| Setting | Reference default | Experiment |
| --- | --- | --- |
| `maxBatteryTempRiseC` (rise over the block's first ready reading) | 2 °C | **10 °C** (the configured maximum) |
| `readinessTimeoutSec` | 180 s | **300 s** |

Unchanged: the 30 s cooldown, 3 repetitions per block, the absolute battery limit
of 40 °C, thermal status `NONE`, headroom ≤ 0.8, and every run-time flag
(including `SEVERE`).

Justification, decided before any data:

1. **With the 2 °C bound, the comparison would be censored, and censored
   unevenly.** On tethered phones this bound already skipped repetitions in a
   2-run × 3-repetition configuration without deliberate charging (see
   docs/RUN-CONDITIONS.md, "Device observations"). Charging heats the battery.
   With 15 repetitions per condition, the bound would therefore skip more A than
   B repetitions, and it would keep only A repetitions that happened to start
   cool. That hides the effect we want to measure, and it reduces n_A.
2. **The question is about legacy rows, which had no temperature gate.** They
   were measured with a cooldown only. The relevant effect is the total effect
   of charging, including the extra heat it produces. 10 °C, the maximum the
   configuration parser accepts, effectively disables the relative rule while
   keeping it recorded and validated.
3. **Safety and symmetry are kept.** The absolute 40 °C limit, thermal status
   `NONE` and the headroom limit still gate both conditions in the same way.
4. **300 s readiness bound.** With the relative rule effectively off, the
   remaining waits are for thermal status, headroom and the 40 °C limit. Earlier
   waits were up to about 101 s. 300 s gives a phone heated by charging time to
   pass these limits instead of being skipped. Skips are still recorded and
   counted for each condition. The time budget still bounds the total.

The driver was updated to match. The capture timeout is now 1500 s per block,
because 3 repetitions can each wait up to 300 s. A block is not started with
less than 12 min of budget left, instead of 6 min. The 3 h per phone limit is
unchanged.

### A1.3 Battery temperature as a pre-registered secondary outcome

Battery temperature for each condition is now a **secondary outcome**, no longer
only a diagnostic. Because of A1.2, temperature is free to differ between the
conditions. For each device, the analysis script reports:

- **battery temperature at the start of each repetition** (the `before`
  snapshot): this is the main secondary outcome;
- the maximum battery temperature during each repetition, and the rise from
  before to after;
- for each: median A, median B, and Δ = median B − median A in °C, with the
  same percentile bootstrap (10,000 resamples, fixed seed);
- decision rule: "charging raises battery temperature" when the 95% CI of B − A
  for the starting temperature lies entirely below 0. A difference of ≥ 1 °C is
  called noteworthy, which is descriptive only and not a decision threshold;
- also reported for each condition: readiness wait time, the number of
  readiness-skipped repetitions with their reasons, the maximum thermal status
  and the maximum thermal headroom.

The primary analysis, the 3% threshold and the recommendation rule are
unchanged. A large temperature difference with an "irrelevant" throughput
verdict would mean that charging heats the phone without changing the
measurements within 3%.

### A1.4 Monitor coverage and repetition length

A repetition is labelled A or B only if Android's status was observed for the
whole run. A measured repetition whose monitor coverage is incomplete is
**excluded** and counted as `excluded:incomplete-coverage`, in the same way as
"mixed". Incomplete coverage means any of these: no monitor, a monitor that was
not stopped cleanly, expiry, dropped ticks, a gap above two intervals, too few
ticks, or a sample count that differs from the tick count. The rule is the
validator's own coverage check (`raw_session._monitor_covered`, the mirror of
`monitorCovered`).

The 1 Hz sampler drifts because it sleeps for a fixed time between samples.
Coverage can therefore fail when load plus inference lasts longer than about
300 s on the S23, and sooner on slower phones. A fix is coming separately. In
this configuration (1B Q4_K_M, about 512 prompt tokens, 128 decode tokens),
earlier S23 and Pixel 9 runs took about 10 s per repetition: roughly 1 s load,
3–3.5 s TTFT and about 5 s decode. Each repetition must stay **under 120 s of
load plus inference**. The analysis reports the longest sampled repetition on
each device. If any repetition exceeds 120 s, that is reported as a deviation,
and coverage exclusions for that device are treated as possibly caused by
drift.

### A1.5 Build identity (replaces the earlier one)

Recorded below, at build time and before any device run.

## Results

Not yet run.

## Deviations

None yet.
