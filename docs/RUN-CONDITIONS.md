# Observed run conditions

First measurement layer for issue #2; not full protocol enforcement.
Each repetition saves native snapshots before loading and after inference,
before releasing the model. Probes run outside timed load/inference.

Recorded: battery percentage/temperature, external-power connection (`charging`),
power saving, Android thermal status, interactive screen, foreground window focus,
keyguard lock and process PSS. Missing/invalid readings are null with an
`unavailable` list. PSS is an endpoint observation, not peak model memory.

`observed-conditions-v1` flags battery below 50%, thermal status severe or worse,
power saving, screen off, missing foreground focus or a locked device. Charging
is recorded but allowed. Flags preserve measurements and set `guardsPassed=false`;
otherwise overall guards remain null, even if these limited checks pass.
The policy is retained in each record; it does not certify comparable conditions.

Sampling waits at most two seconds. Timeout/busy results abort the configuration
after saving a failed attempt and releasing the engine. A single native in-flight
request prevents abandoned probes from accumulating or overlapping later inference.
Missing/rejecting probes return unknown readings. The activity keeps its screen
on while visible; it does not change system settings or bypass the lock screen.

Still open in #2: bounded thermal-readiness waiting, headroom, continuous condition
and peak-memory sampling, balanced run order, and a publication charging policy.
Endpoint checks can miss changes between samples. Publication remains blocked.

API semantics: [Android thermal status](https://developer.android.com/reference/android/os/PowerManager#getCurrentThermalStatus())
and [process PSS](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss()).
