# CPU reference runs

The default app runs the versioned configuration in
[`cpu-reference-v1.json`](../xebenchapp/configs/cpu-reference-v1.json).
This first reference path supports **llama.cpp CPU**. Other engine adapters remain
experimental; expanding reference coverage is a follow-up in issue #3.

Build the `reference` flavor, which packages only llama.rn's CPU libraries. It
needs no vendor SDK and no source or packaging edits; prerequisites are listed in
the README's [build flavors](../README.md#build-flavors-and-prerequisites) section.
From `xebenchapp/android`:

```sh
./gradlew assembleReferenceRelease
python3 ../../scripts/check_reference_apk.py app/build/outputs/apk/reference/release/app-reference-release.apk
```

A successful Gradle build alone does not prove that the runtime libraries were
packaged. The check fails if any llama.rn CPU variant or the JS bundle is missing,
or if the APK carries a native library outside React Native core and llama.rn CPU
(for example Hexagon/HTP, OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X or ExecuTorch) or a
LiteRT-LM/GENIE-X engine bridge. ExecuTorch's Java glue remains because autolinking
is not flavor-aware. The application ID stays `com.xebenchapp`.

## Configure and capture

Install the app first. Then stage the pinned reference GGUF in the
app's external files directory:

```sh
python3 scripts/stage_model.py --serial <serial>   # default: models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
```

The script refuses a file whose name, size or SHA-256 differs from
[`configs/models/reference-models.json`](../configs/models/reference-models.json).
It also refuses when the pushed copy fails a `sha256sum` check on the device. It
never downloads the model. [MODELS.md](../MODELS.md) gives the license terms and the
download command for the pinned revision.

To customize a run, copy the bundled JSON and edit its model basename, quant
label, threads/context, repetition, cooldown, readiness timeout
(`readinessTimeoutSec`) or battery-rise bound (`maxBatteryTempRiseC`) settings, or
add `orderSeed` to reproduce a recorded run order. `stage_model.py` stages only
models listed in the manifest. Push any other GGUF with `adb` yourself; runs with
it are not reference runs. Then stage the configuration:

```sh
adb -s <serial> push my-config.json /sdcard/Android/data/com.xebenchapp/files/xebench-config.json
scripts/capture_app_run.sh <serial> 900
```

The staged configuration persists across launches; if absent, the app uses the
bundled default. Unknown fields, unsupported engines, paths/URLs and invalid
limits fail before model loading. Each repetition waits for measured
[readiness](RUN-CONDITIONS.md) and is sampled while it runs; overall guards stay
**unverified** unless a failure is known, because backend execution is unverified.
Model hashes identify the bytes; model/quant labels remain operator-supplied.

## Evidence

- Each session records the complete configuration, prompt, model SHA-256/size,
  binding version, native token counters and every attempted repetition.
- Successful repetitions retain load time, TTFT, throughput and timing methods.
  Summaries include median, IQR, min/max and actual sample count. Quartiles use
  linear interpolation at index `(n - 1) * p`.
- A checkpoint is saved after each repetition and before cooldown. Failures
  retain completed repetitions and release the engine before stopping the session.
  Cleanup failure aborts the whole configuration after saving both errors, if any.
  Readiness timeouts are saved as `skipped` repetitions. Runs that are not measured
  after an abort, or whose model cannot be read, are saved as failed sessions with
  a `stopReason`. The executed run order and its seed are recorded.
- Runs without positive timed decode work are failed, not reported as zero speed.
  A one-token limit can hit this case because the first token comes from prefill.
  Decode counters describe timed evaluations, not the total generated output.
- Files live under `xebench-results/` in the app's external directory. Logcat
  carries short filename/checksum references; host capture verifies and retrieves
  them without changing their bytes. Local manifests distinguish checkpoints from
  terminal records and carry host-collected device facts separately.
- Capture fingerprints installed base/split APKs before launch and after completion.
  The manifest's `appIdentity` reports matched, changed or unverified snapshots;
  paths are omitted. A changed APK returns exit 4 while preserving evidence.
  Missing probes and interrupted runs cannot establish a match. Matching snapshots
  identify installed bytes, not source provenance or continuous runtime attestation.

“Cold” means a fresh engine instance in a warm process. Hashing the model before
measurement warms the OS file cache. Actual backend execution, peak memory, quality,
and full native build identity remain unverified. Condition sampling is 1 Hz, not
continuous.

Completed sessions can be previewed with `aggregate.mjs --pretty`; `--update`
rejects these new records until publication validation and consumer migration are
implemented. Raw records and diagnostics stay local; only a sanitized
[evidence export](#evidence-export) may leave the machine. Historical data is unchanged.

## Host validation

Capture validates every `schemaVersion: 1` record, checkpoints included, against
[`raw-session-v1.json`](../schemas/raw-session-v1.json) plus cross-field rules in
[`raw_session.py`](../scripts/raw_session.py). It rejects malformed, truncated or
non-finite JSON, unknown versions and fields, a repetition count or `runIndex`
sequence that disagrees with `nColdRuns`, and inconsistent status. `running` has
fewer than the configured attempts, all successful; `complete` has all of them;
`failed` ends at its only failed attempt. Summaries are recomputed from successful
repetitions with the interpolation above. The record must also agree with its
configured run, and failed condition checks require `guardsPassed=false`.
Per-attempt conditions, `conditionPolicy`, `protocol` and `configuration` accept
additional fields. Records from apps built before condition snapshots omit all
condition evidence and keep `guardsPassed=null`; a mix is rejected.

`protocol.version` selects the schema branch. `cpu-reference-v1` records keep the
rules above unchanged (frozen fixture `tests/fixtures/raw-session/app-emitted-v1.json`).
`cpu-reference-v2` records (harness `xebench-0.6-reference`) must also carry
readiness, sampling, instance, order and verdict evidence. For these, the validator:

- checks `status` against `stopReason`: a skipped repetition ends a
  `readiness-timeout` session, and unmeasured runs have no attempts and
  `artifact: null`;
- requires the published `run-conditions-v2` values and `charging-policy-v3` (or the
  earlier status-only `charging-policy-v2`), and uses the configured
  `maxBatteryTempRiseC`;
- types and ranges every snapshot and sample field as the app does;
- recomputes the monitor summary from persisted samples, or bounds it when the
  samples were decimated;
- requires exactly the guard notes implied by the verdict;
- recomputes every endpoint, readiness and sampling assessment, the condition
  verdict and its charging status from the recorded snapshots and monitor summaries,
  so unknown evidence cannot be recorded as ready or passing;
- reproduces the seeded run order.

A rejected payload stays in `invalid-<id>.txt` beside `invalid-<id>.reason.json`
(reason codes, JSON Pointer paths, messages); the manifest lists `rejections`.
A retrieved device file is stored as retrieved, with no added newline. Its reason
file keeps the `sourceFile` and `claimedSha256` from the logcat reference, so the
device checksum can be re-checked from saved evidence. This holds for UTF-8 records
without carriage returns, because adb output is read as text. An oversized file
keeps only its bounded prefix.
A rejection prevents a `complete` capture: after `XEBENCH_DONE` it is `partial`.
Exit codes: 0 complete, 2 setup, startup, capture or cleanup failure, 3 timeout,
4 partial or app-changed, 130/143 interrupted. Manifest entries carry `validation`:
`raw-session-v1`, or `legacy-unvalidated` for inline records without
`schemaVersion`; file references must be v1. Validation is not
comparison eligibility; `publicationReady` stays false. To check files offline, run
`python3 scripts/raw_session.py <file>...`. After an intended record change, run
`XEBENCH_UPDATE_FIXTURES=1 npx jest __tests__/rawSessionSchema.test.ts` in
`xebenchapp` and update the schema; CI fails while the two disagree.

## Evidence export

Capture directories are local diagnostics. Nothing in them is public until it is
exported. [`export_evidence.py`](../scripts/export_evidence.py) is the only path
from a capture to a shareable artifact:

```sh
python3 scripts/export_evidence.py export --out results/exports results/<captureId>...
python3 scripts/export_evidence.py verify results/exports/<bundleSha256>
```

Export fails closed (exit 1, nothing written) if any check fails. It checks every
listed record and checkpoint against its capture checksum and re-validates it with
`raw_session.py`. It refuses legacy records, unlisted record files, inconsistent
counts, duplicate sessions and captures without a terminal record. It also refuses
an approved field whose value does not have the expected form. A final screen
refuses an export if any output string looks like an email, local path, token,
ANDROID_ID-like hex or network serial. Refusal messages name inputs by position
and JSON location, never by value. A complete capture must have no errors,
rejections or failures.

Only allowlisted fields are copied. Unknown fields are dropped and listed in the
report on stdout, which stays local. Reason codes, rejection codes, signal names,
guard notes, enumerated values (statuses, methods, phases, modes) and the cold-run
definition come from a closed app/host vocabulary. Other values become `unrecognized`.

- **Exported:** session and configuration identity, protocol with `promptSha256`
  in place of the prompt text, engine and version, model SHA-256, every repetition
  (metrics, token counts, methods, failure phase), condition snapshots including
  battery temperature, policies, verdicts and summaries. Device identity is
  limited to manufacturer, model, SoC and Android release. A capture contributes
  its status, counts, rejection reason-code counts, and APK names with their SHA-256.
- **Never exported:** serials, ANDROID_ID, the `ro.product.device` codename,
  install and host paths, usernames, logcat, error logs, error and cleanup
  messages, invalid payloads and their reason files, and `publicationReady`.

The bundle `<bundleSha256>/` holds `manifest.json`, `captures/<captureId>.json` and
`sessions/<runId>.json`. Every file is canonical JSON: sorted keys, `,`/`:`
separators, ASCII only, no NaN or Infinity, and one trailing LF. The files contain
no export time, host or path, so the same captures always give the same bytes.
**`bundleSha256` is the SHA-256 of the bytes of `manifest.json`.** The manifest
lists every other file with its SHA-256 and size. The export prints
`{sessionIds, bundleSha256}`, plus `url` when `--base-url https://...` is given,
for a verified row's `evidence`. To check a bundle, hash the bytes as served;
never re-serialize them.

`verify` checks the following:

- the hash, every checksum and size, and any unlisted or symlinked files;
- canonical form, and allowlist conformance of the manifest and every file;
- the privacy screen;
- that each file name is bound to its ID;
- repetition numbering and count against `nColdRuns`, and status against the repetitions;
- that capture counts agree with the sessions;
- every summary, recomputed from the repetitions with the interpolation above.

With `--session ID`, it also checks that a row's sessions are in the bundle. A
malformed bundle produces errors and exit 1, never a crash. `verify` does not
re-run the full raw-schema validation, because the raw record is not exported.

Approved labels (model, quant, engine version, prompt label, policy versions,
configuration and run IDs) and device properties are copied verbatim once they pass the form checks and the screen. A serial typed
into a label cannot be detected. Exporting a session does not make it eligible
for publication.
