# CPU reference runs

The default app runs the versioned configuration in
[`cpu-reference-v1.json`](../xebenchapp/configs/cpu-reference-v1.json).
This first reference path supports **llama.cpp CPU**. Other engine adapters remain
experimental; expanding reference coverage and separating native build flavors
are follow-ups in issue #3.

Build prerequisites: run `npm ci` in `xebenchapp` with install scripts enabled;
llama.rn downloads checksum-verified native libraries during installation.
If dependencies were installed with `--ignore-scripts` for unit tests, run
`npm rebuild llama.rn --foreground-scripts` before building an APK. A successful
Gradle build alone does not prove that these runtime libraries were packaged.
Check the arm64 APK before installation with
`python3 scripts/check_reference_apk.py <apk>` from the repository root.

## Configure and capture

Stage the GGUF named by the configuration in the app's external files directory
(see [MODELS.md](../MODELS.md)). To customize a run, copy the bundled JSON, edit its
model basename, quant label, threads/context or repetition settings, then stage it:

```sh
adb -s <serial> push my-config.json /sdcard/Android/data/com.xebenchapp/files/xebench-config.json
scripts/capture_app_run.sh <serial> 900
```

The staged configuration persists across launches; if absent, the app uses the
bundled default. Unknown fields, unsupported engines, paths/URLs and invalid
limits fail before model loading. Run conditions are **unverified**, not passed.
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
- Runs without positive timed decode work are failed, not reported as zero speed.
  A one-token limit can hit this case because the first token comes from prefill.
  Decode counters describe timed evaluations, not the total generated output.
- Files live under `xebench-results/` in the app's external directory. Logcat
  carries short filename/checksum references; host capture verifies and retrieves
  them without changing their bytes. Local manifests distinguish checkpoints from
  terminal records and carry host-collected device facts separately.

“Cold” means a fresh engine instance in a warm process. Hashing the model before
measurement warms the OS file cache. Actual backend execution, thermal conditions,
memory, quality, and full native build identity are not yet verified here.

Completed sessions can be previewed with `aggregate.mjs --pretty`; `--update`
rejects these new records until publication validation and consumer migration are
implemented. Raw records and diagnostics stay local. Historical data is unchanged.
