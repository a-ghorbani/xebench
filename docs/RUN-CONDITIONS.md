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
3. **Sampling:** a native background thread samples immediately and then at fixed
   rate, at start + k × 1 s, from just before `load()` until `benchOnce()` returns.
   Sampling cost therefore does not accumulate into drift, and a late wake-up skips
   missed slots instead of bursting. No JS-bridge sampling call happens between the
   start of load and the end of inference.
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

A session record's `guardsPassed` is never `true`: a condition pass is necessary but
not sufficient. A published row's `guardsPassed` is decided separately, by
[publication policy v1](#publication-policy-v1).

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
  measured repetition was sampled throughout under the coverage rule above. An
  expired, gappy or incomplete monitor leaves the unsampled stretch `unknown`, even
  when every persisted sample reads `FULL`;
- `unknown` otherwise. Unknown is never eligible.

`charging-policy-v2`, written by earlier builds of this work, applies the same
rules, including the coverage rule, but counts `DISCHARGING` as unknown. The
validator still accepts it, with those semantics.

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
| Cooldown before every repetition, the first included | `session.ts` `runSession`; `raw_session.py` `attempts.cooldown` | `session.test.ts` checkpoints before cooldown; [`reference.test.ts`](../xebenchapp/__tests__/reference.test.ts) cooldown after hashing; `test_raw_session.py` first-repetition cooldown |
| Sampling outside JS during timed work; fixed rate, bounded and expiring | [`BenchConditionsModule.kt`](../xebenchapp/android/app/src/main/java/com/xebenchapp/BenchConditionsModule.kt) `startMonitor`/`stopMonitor`; [`MonitorSchedule.kt`](../xebenchapp/android/app/src/main/java/com/xebenchapp/MonitorSchedule.kt); `conditions.ts` `normalizeMonitor` | `session.test.ts` event order, incomplete coverage; `conditions.test.ts` bounded samples, coverage, long fixed-rate runs; [`MonitorScheduleTest.kt`](../xebenchapp/android/app/src/test/java/com/xebenchapp/MonitorScheduleTest.kt); device runs |
| Interruption detection | [`MainActivity.kt`](../xebenchapp/android/app/src/main/java/com/xebenchapp/MainActivity.kt) focus counter; `assessDuring` | `conditions.test.ts` focus losses; `session.test.ts` interruption then readiness wait; device runs |
| Unresponsive probes/monitors abort after save and release | `conditions.ts` `bounded`; `session.ts` | `conditions.test.ts` unresponsive calls; `session.test.ts` late probes, busy sampler, monitor start/stop failures |
| Cleanup on failure; unmeasured runs recorded | `session.ts`, `reference.ts` | `session.test.ts` failed load/inference/release, monitor stop on failure; `reference.test.ts` cleanup abort, unreadable model |
| Charging policy: tethered allowed, charging never eligible, unknown (including unsampled stretches) never eligible, wait while charging | `conditions.ts` `chargingState`, `assessCharging`, `assessReadiness`, `monitorCovered`; `session.ts` `chargingCounts`, `conditionVerdict`; `BenchConditionsModule.kt` raw status/plugged/current | `conditions.test.ts` charging-policy-v3; `session.test.ts` charging-policy-v3 in sessions, incomplete sampling; `test_raw_session.py` charging recompute |
| Instance/process identity, seeded run order | `session.ts`; [`order.ts`](../xebenchapp/src/harness/order.ts) | `session.test.ts` identity; [`order.test.ts`](../xebenchapp/__tests__/order.test.ts); `reference.test.ts` run order |
| Records cannot claim what their evidence does not support | [`raw_session.py`](../scripts/raw_session.py) `_semantic_v2` recomputes assessments, readiness, verdict and order | [`test_raw_session.py`](../tests/test_raw_session.py) `ConditionsV2Tests`; [`rawSessionSchema.test.ts`](../xebenchapp/__tests__/rawSessionSchema.test.ts) drift fixture |
| v2 evidence survives sanitized export | [`export_evidence.py`](../scripts/export_evidence.py) allowlist (snapshot, monitor, readiness, charging, verdict, order) | [`test_export_evidence.py`](../tests/test_export_evidence.py) `ConditionsV2ExportTests` (capture → validation → export → verify) |

The monitor's fixed-rate schedule has a JVM unit test (`MonitorScheduleTest`,
run with `./gradlew :app:testReferenceReleaseUnitTest`). It covers runs up to
15 min at a realistic 14 ms per sample, and shows that the earlier fixed-delay
loop fell out of coverage. CI has no Android SDK, so the test runs locally. The
other native rules (expiry, single-flight, focus counter) have no JVM tests. They
are validated by the device runs listed in the #2 pull requests.

## Device observations (named devices, not general limits)

On a Pixel 9 (AC power) and a Galaxy S23 (USB power), idle thermal status was 0
and headroom about 0.44–0.63. One sample cost about 2–5 ms; the first sample was
up to about 54 ms. Battery temperature rose about 2–3 °C over five or six
repetitions and did not fall back within the 180 s readiness bound. The 2 °C
rise rule therefore delayed (up to 101 s) and then skipped a repetition of the
second run of a two-run, three-repetition configuration on both devices
(`battery-not-cooled`). This is the intended
behavior, but it limits configuration length on tethered devices. The owner kept the
2 °C default. The bound is now configurable per configuration. The 2 °C default
stays for reference runs; the charging A/B experiment's 10 °C override was
experiment-only ([decision](experiments/charging-ab.md#decision-2026-10-17)).

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

With fixed-rate sampling, a long OnePlus 6 run (one thread, 512 decode tokens)
kept full coverage over 139.7 s: 140 ticks, at most 1001 ms between samples, and
about 5 ms per sample on average (at most about 9 ms). With the fixed-delay loop
it replaced, an earlier 43 s capture on this device (about 14 ms per sample) had
no tick-count margin left.

## Limits

The battery-rise baseline is checked per record. The validator recomputes readiness
against each record's `baselineBatteryTempC`, but cannot tell whether that value is
the first ready reading of the configuration invocation. The baseline is shared
across the sessions of one invocation, so a falsified high baseline in one record
still validates. A cross-record check over a capture is future work.

Thresholds are protocol choices, not device-calibrated limits. Battery temperature
is a proxy for device temperature. Headroom support and scale vary by device. A
1 Hz sampler can miss sub-second thermal or battery changes; only focus losses
are counted between samples. Device validation covers the devices named in the
pull request, not other models.

## Publication policy v1

Policy `publication-policy-v1` decides the `validation` and `guardsPassed` of a row
published from an evidence bundle (`scripts/aggregate.mjs --from-bundles`).
[`publication_policy.py`](../scripts/publication_policy.py) `evaluate()` is its only
implementation, and
[`test_publication_policy.py`](../tests/test_publication_policy.py) tests every
check alone and every pair of checks.

The validator recomputes every input from the bundle's evidence and never trusts a
recorded verdict, `guardsPassed` or summary. It first runs `export_evidence.py
verify`, then re-runs the raw-session cross-field rules on each sanitized session:
the configured run behind the record (engine, model, quant, model file, threads and
context), repetition numbering, the summary, readiness, condition assessments, monitor
summaries, charging, verdict, run order and, for cpu-reference-v3 and accelerator-v2,
build and execution identity. Accelerator gates are re-checked from the exported probe
verdicts, because a bundle withholds prompt and output text. The capture's APK identity
is recomputed from its before and after snapshots. The values the raw-session schema
fixes are checked too: every session runs on Android, and a cpu-reference session
measures the CPU only. The device model the app reports must equal the model the host
read from the phone. Where a repetition times prefill or decode with `engine-timings`
and keeps the engine's counters (`engineInternal`; LiteRT-LM keeps none), the published
`prefillTps` and `decodeTps` must equal `promptPerSecond` and `predictedPerSecond`. A
session whose SoC contradicts its device model's entry in
[`configs/devices.json`](../configs/devices.json) is refused (SoC names compare
case-insensitively). A bundle whose evidence disagrees with its own claims is refused
as a whole.

The policy also anchors each row to reviewed reference data in this repository: the
run configurations under [`xebenchapp/configs/`](../xebenchapp/configs/) (except
`experiment-*`), the model manifests
[`configs/models/*-models.json`](../configs/models/) (their `models` and
`pinnedArtifacts`) and the device table. Without
that data no bundle is decided. A configuration file that a published session names
is therefore never edited: a changed run configuration is a new file, or the published
rows stop reproducing.

A row earns `guardsPassed: true` and `validation: "verified"` only when every check
passes:

| Check | Pass | Fail | Unknown |
| --- | --- | --- | --- |
| `complete` | status `complete` with every planned repetition measured | the session stopped early | anything else |
| `conditions` | every measured repetition, and the session, recompute to verdict `pass` | a recomputed `fail` | `unverified`, or no condition evidence (cpu-reference-v1) |
| `charging` | every measured repetition recomputes to `satisfied` under `charging-policy-v3` | `violated` or `on-battery` | `unknown`, or another charging policy |
| `coverage` | every measured repetition was sampled throughout (the coverage rule above) | — | a gap, expiry, dropped tick or missing monitor |
| `artifact` | the session's `configurationSource` hash names a reviewed configuration file, the session carries that file's content, its configured run (engine, backend, model, quant, model file, `modelSha256`) is exactly a run of that file, and the artifact SHA-256 equals both that `modelSha256` and the model manifest entry for the file | the artifact differs from the configured `modelSha256` or from the manifest entry | the run pins no `modelSha256`, the session names no reviewed configuration file or carries other content, the run is not in that file, or its file has no manifest entry |
| `backend` | CPU: `confirmed` or `consistent`, and for llama.cpp no offloaded layer or device in the effective settings. GPU/NPU: `confirmed` | `mismatch`, or a llama.cpp CPU run that offloads layers | anything else, including `consistent` on GPU/NPU and llama.cpp CPU runs without effective settings; LiteRT-LM GPU/NPU evidence is at most `consistent` |
| `correctness` (accelerator protocols) | output gate `pass` | `fail` | `unverified` |
| `comparison` (accelerator protocols) | `valid` | `invalid` | `unverified` |
| `build` | reported release build, clean tree (`gitDirty: false`), known commit, flavor `reference`, `experimental` or `geniex` | dirty tree | not reported, any field unknown, or a debug build (its JavaScript comes from Metro, so the commit does not identify the code that ran) |
| `app-identity` | two recorded, identical APK snapshots (before and after the run) | the snapshots differ, or the capture reports `changed` | a missing or unrecorded snapshot |
| `device` | the device model is in `configs/devices.json` and the capture's SoC matches it | — (a contradiction refuses the bundle) | the model is not in the table, or no SoC is reported |

Backend evidence is recomputed with the `backend.ts` rule: from each repetition's
`observedBackend` for accelerator protocols, and from each load's execution report
for cpu-reference-v3 llama.cpp runs. llama.rn's GPU flag says only that GPU devices
are attached, so a llama.cpp CPU run also needs effective settings with
`n_gpu_layers` 0 and no device. A repetition without evidence leaves the check
unknown. `confirmed` is the engine's own device report, not per-operation execution.
Correctness and comparison are recomputed from the exported probe verdicts.

Any failed check gives `validation: "failed"` and `guardsPassed: false`. Otherwise,
any unknown check gives `validation: "unverified"` and `guardsPassed: null`. Unknown
is never a pass. Three kinds of session are never published at all:

- experiments: `record-only-experiment` charging readiness, an
  `experimentChargingReadiness` configuration, or a configuration id starting
  `experiment-`;
- bug reproductions (`purpose: "bug-repro"`);
- sessions without a measured repetition.

A row published from a bundle names its configuration (`configurationId`,
`configurationSha256`) only when the session's `configurationSource` hash names a committed
file under `xebenchapp/configs/` and the session carries exactly that file's content; a session
that names no such file names no configuration and is never displayed. Which published rows the
board displays is decided by the measurement sets below.

A published session is never rewritten, and CI re-derives every published row from its
bundle with the current code. A change to the policy, the row format or its rounding
therefore comes with a new policy version and a migration of the published index.

Limits: for engines whose only timing source is the engine's own benchmark API
(LiteRT-LM's `getBenchmarkInfo`), the counter cross-check does not apply: that API is the
measurement, and no independent counter exists. The gate's probe verdicts are recomputed from model outputs by the raw-session
validator at capture time; a bundle carries only their digests, so the publication
check re-derives the gate status from the verdicts, not from the outputs. The policy
does not establish cross-device comparability or quality beyond the gate.

## Measurement sets

Owner decision, 2026-10-19. What the board at pocketpal.dev/leaderboard/engines displays is
declared in [`configs/board.json`](../configs/board.json) and applied by
[`scripts/lib/sets.mjs`](../scripts/lib/sets.mjs) on every `aggregate.mjs` write.

1. **One set per engine.** An engine's board rows all come from one measurement set: one engine
   version, one binding version and one measurement campaign. Different engines may be at
   different versions. `engines.<engine>` declares the set:
   - `"set": "sessions"`: sessions published from evidence bundles whose row names one of
     `configurations` and runs one of that configuration's `backends`, whose `build` carries
     exactly `engineVersion` and `bindingVersion` (none when not declared), measured on or after
     `measuredFrom` (UTC). The backends keep a configuration's other runs off the board: an
     accelerator configuration's CPU reference run has the same row key as a CPU board row, so
     `llamacpp-llama32-q4km-hexagon-v1` displays `npu` only, `llamacpp-qwen25-opencl-v1` `gpu`
     only and `cpu-reference-v3*` `cpu` only.
   - `"set": "pre-version-recording"`: the engine's rows from before the app recorded engine
     versions, all measured in the month `measured`. They show no version, and none is
     backfilled. Today ExecuTorch, LiteRT-LM and GENIE-X display their July 2026 rows.
2. **Latest set only.** Within a set, a key displays its newest verified session, else its
   newest unverified one. A failed session or row is never displayed. `data/benchmarks.json`
   holds only displayed rows; everything else (staged and superseded sessions, replaced and
   retired rows) is in [`data/sessions/index.json`](../data/sessions/index.json).
3. **Re-measure everything, or retire it.** Re-measuring an engine means re-measuring all of its
   board setups. A setup that cannot be measured is listed in `retiredKeys` with a reason and a
   date. The reason stays in the data (`configs/board.json` and the row's `replacedRows` entry
   with `reason: "retired"`), never on the page.
4. **Complete or nothing.** An update that would take a displayed key off the board refuses
   unless the key is retired. So a new set is displayed only once every displayed key of the
   engine has a session in it or is retired; until then, `aggregate.mjs` names every missing key.
5. **Months, always.** Every measured row carries `sourceDate` (YYYY-MM). A row without versions
   also carries `dateBasis` (contract 1.3): `measured` when the month comes from the run's own
   record, or `first-published` when no record survives and the month is that of the first
   commit carrying the row. [`scripts/lib/legacy-dates.mjs`](../scripts/lib/legacy-dates.mjs)
   freezes each July 2026 row (key, numbers, month and basis); a row without versions is displayed
   only as one of those rows, unchanged, and
   [`scripts/date_legacy_rows.mjs`](../scripts/date_legacy_rows.mjs) writes their months.

### Publishing a new set

1. Publish the new version's sessions into the history while the board still shows the old
   set: `aggregate.mjs --from-bundles evidence/ --session <id>... --update --stage`. A staged
   session changes nothing on the board. Without `--stage`, a session outside its engine's set is
   refused; a session of a configuration or backend the set does not list needs
   `--allow-unlisted-configuration` instead.
2. In one publication pull request, edit `engines.<engine>` to the new versions, configurations
   and `measuredFrom`, add every setup that was not re-measured to `retiredKeys`, and run
   `aggregate.mjs --apply-board --update --replace-legacy` (or publish the last sessions with
   `--update`, which applies the board too). The run refuses, naming each key, until the set is
   complete; then it writes both files together.
3. `validate_data.mjs` (CI) checks that applying `configs/board.json` to the committed files
   changes nothing.

### The llama.cpp campaign (llama.rn 0.13.0-rc.7)

Sets were declared while this campaign was in progress, so `engines["llama.cpp"]` declares the
rc.7 set (`llama.cpp b11385 (bf9a0cc)` + `llama.rn 0.13.0-rc.7`, `cpu-reference-v3`, CPU) with a
`carryover`: the nine July 2026 llama.cpp rows stay displayed next to the nine rc.7 CPU rows until
the campaign finishes. The carryover names the set's own versions, so moving the set to other
versions ends it, and it displays only the frozen July rows of
[`scripts/lib/legacy-dates.mjs`](../scripts/lib/legacy-dates.mjs), unchanged; no other row
without versions is ever displayed. A session of the set for a carried key replaces its row
(`--replace-legacy`). The July Hexagon row (POCO F8
Ultra, Llama 3.2 1B Q4_0, failed) is retired: it is replaced by a Q4_K_M NPU key. The campaign
finishes in one publication pull request that

- adds the new configurations to the set, each with its backends (`cpu-reference-v3-qwen25` and
  `cpu-reference-v3-gemma3`: `cpu`; `llamacpp-qwen25-opencl-v1`: `gpu`;
  `llamacpp-llama32-q4km-hexagon-v1`: `npu`);
- publishes the rc.7 sessions for the carried setups with `--replace-legacy`;
- lists every carried setup that is not re-measured in `retiredKeys` and deletes `carryover`;
- runs `aggregate.mjs --update` once. It refuses, naming each July row still neither replaced nor
  retired, until the list is complete.

API semantics: [thermal status](https://developer.android.com/reference/android/os/PowerManager#getCurrentThermalStatus()),
[thermal headroom](https://developer.android.com/reference/android/os/PowerManager#getThermalHeadroom(int)),
[process PSS](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss()).
