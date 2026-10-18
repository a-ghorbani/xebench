# CPU reference runs

The default app runs the versioned configuration in
[`cpu-reference-v2.json`](../xebenchapp/configs/cpu-reference-v2.json), which every flavor
bundles. [`cpu-reference-v1.json`](../xebenchapp/configs/cpu-reference-v1.json) runs as a
staged configuration. Both record sessions of protocol `cpu-reference-v3`; the
configuration IDs are separate from the protocol versions of the same names described
under [Host validation](#host-validation). This first reference path supports
**llama.cpp CPU**.

The two configurations differ only in how llama.rn loads the model and computes attention:

| Run field | llama.rn parameter | cpu-reference-v1 | cpu-reference-v2 |
| --- | --- | --- | --- |
| `useMmap` | `use_mmap` | not set (`true`) | `false` |
| `useMlock` | `use_mlock` | not set (`false`) | `false` |
| `noExtraBufts` | `no_extra_bufts` | not set (`false`, weight repacking on) | `false` |
| `flashAttnType` | `flash_attn_type` | not set (`auto`) | `off` |

A run field that is not set takes the harness default in parentheses
(`LLAMA_RN_DEFAULTS` in [`LlamaRnAdapter.ts`](../xebenchapp/src/harness/adapters/LlamaRnAdapter.ts)).
With llama.rn 0.13.0-rc.7, `use_mmap: true` selects memory-mapped loading
(`LLAMA_LOAD_MODE_MMAP`), and `use_mmap: false` with `use_mlock: false` reads the weights
into memory (`LLAMA_LOAD_MODE_NONE`; `cpp/jsi/JSIParams.cpp`). `no_extra_bufts: false` lets
llama.cpp repack weights into its extra CPU buffer types; it is also the binding's default.

cpu-reference-v2's four values are PocketPal's Android defaults in
[PocketPal](https://github.com/a-ghorbani/pocketpal-ai) 1.20.0 (commit `07ef06f`).
`createDefaultContextInitParams()` in `src/utils/contextInitParamsVersions.ts` sets
`use_mmap: 'false'` and `flash_attn_type: 'off'` on Android, `use_mlock: false` and
`no_extra_bufts: false`. `ModelStore.getEffectiveContextInitParams()` in
`src/store/ModelStore.ts` passes them to `initLlama()`, with `'false'` resolved to
`use_mmap: false`. Every other setting stays the CPU reference's: `n_gpu_layers` 0,
`n_threads` 4, `n_ctx` 2048, `n_batch` 512, `n_ubatch` 512, f16 K/V caches, greedy sampling,
and the prompt and decode length of cpu-reference-v1. PocketPal's defaults for `n_ctx`,
`n_batch`, `n_ubatch` and the K/V cache types are the same. Its other `initLlama()`
parameters are not adopted, among them `n_gpu_layers: 99` with automatic devices,
`kv_unified: true`, `n_parallel: 1`, `state_cache_budget_mb` and the per-device thread count
it sets on first launch; the harness leaves the ones it does not list to the binding.
For this single-sequence CPU run, `kv_unified`, `n_parallel` and `state_cache_budget_mb`
change nothing. llama.rn's own `n_parallel` default is also 1. With one sequence, the KV
cache has one stream and a 2048-token context per sequence, unified or not. The prompt state
cache only reuses state across turns on recurrent, hybrid or SWA models. Llama 3.2 is none of
these, and every repetition loads a fresh instance.

Other engine adapters remain experimental; expanding reference coverage is a follow-up in
issue #3.

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

## Build identity and reproducibility

The APK embeds its git commit, a dirty flag, its flavor, build type, `versionName`
and the SHA-256 of the bundled configuration (`BuildConfig`). Every session records
them, plus the SHA-256 of each certificate that signed the installed APK (see
[Evidence](#evidence)). A dirty build cannot be rebuilt from its commit, so treat
`gitDirty: true` as unreproducible.

CI ([`android-build.yml`](../.github/workflows/android-build.yml)) builds
`assembleReferenceRelease` (arm64-v8a) for the pull-request head commit, runs the
APK check and uploads the APK with its SHA-256, the signing certificate and the
embedded build identity. To compare a local build, check out that commit with a
clean tree, build with the same JDK major version, and compare SHA-256s.

**The APK SHA-256 depends on the signing key.** The `release` build type is signed
with the checked-in `android/app/debug.keystore` (certificate SHA-256
`fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`), so anyone can
reproduce the signed bytes. An APK signed with any other key has a different SHA-256
even when its contents are identical. Compare unsigned contents in that case, for
example with `apksigner verify --print-certs` plus a diff of the unzipped entries
other than `META-INF/`. The capture manifest's `appIdentity` records the installed
APK hashes, and the session's `build.signingCertSha256` says which key signed them.
Bit-for-bit reproducibility is measured, not guaranteed: the build is not
hermetic (host NDK/SDK, JDK), so a mismatch is informative, not a failure.

Measured on PR #24 (CI on x86-64 Ubuntu with Temurin 21, against a local aarch64
Linux host with OpenJDK 21, same commit and both clean): the APK SHA-256s differ.
The dex files, the Hermes bundle, `resources.arsc` and every other asset are
identical. The difference is confined to the eight libraries compiled by CMake during
the build (`libappmodules.so`, `libreact_codegen_safeareacontext.so` and llama.rn's
`librnllama_jni*.so`). Seven differ only in their 20-byte GNU build ID, which hashes
host-specific build paths. `libreact_codegen_safeareacontext.so` also embeds the
absolute path of a React Native header in the Gradle cache, which includes the
build user's home directory. Since llama.rn 0.13.0-rc.7 compiles its core from
source, every `librnllama*.so` also embeds the absolute paths of its source files
(about 165 per library, e.g. `<checkout>/xebenchapp/node_modules/llama.rn/cpp/...`),
so a local build discloses its checkout path and user name in six libraries. Do not
distribute an APK built under a home directory you do not want to disclose; share
CI-built APKs (`/home/runner/...`) instead. Release builds set React Native's dev-server IP
resource to `localhost`, so the build host's address is not in the APK. Removing
the path dependence from native builds is a follow-up.

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
label, threads/context, load settings (`useMmap`, `useMlock`, `noExtraBufts`,
`flashAttnType`), repetition, cooldown, readiness timeout
(`readinessTimeoutSec`) or battery-rise bound (`maxBatteryTempRiseC`) settings, or
add `orderSeed` to reproduce a recorded run order. To run cpu-reference-v1, stage the
committed file byte for byte, so the recorded `configurationSource` hash names it. A run
may pin `modelSha256` (64 lowercase hex); the bundled configuration pins the reference
GGUF from the manifest. `stage_model.py` stages only models listed in the manifest. Push any other
GGUF with `adb` yourself, and remove or replace its `modelSha256`; runs with it are
not reference runs. Then stage the configuration:

```sh
adb -s <serial> push my-config.json /sdcard/Android/data/com.xebenchapp/files/xebench-config.json
scripts/capture_app_run.sh <serial> 900
```

The staged configuration persists across launches; if absent, the app uses the
bundled default. Unknown fields, unsupported engines, paths/URLs and invalid
limits fail before model loading. A model whose SHA-256 differs from the run's
`modelSha256` is never loaded: the run is saved as a failed `model-hash-mismatch`
session with both hashes, and every later run is saved as `configuration-aborted`.
Each repetition waits for measured [readiness](RUN-CONDITIONS.md) and is sampled
while it runs; overall guards stay
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
- Records of protocol `cpu-reference-v3` (harness `xebench-0.7-reference`) also
  identify the binary, the configuration and what the engine was given:
  - `build`: commit SHA and dirty flag, flavor, build type, `versionName`/`versionCode`
    and the SHA-256 of each APK signing certificate, embedded at build time and
    read through the `BuildInfo` native module. Without the module it is
    `status: "unavailable"` with every field null; unknown values are never guessed.
  - `configurationSource`: `bundled` or `staged`, with the SHA-256 of the file's
    bytes. A staged file is hashed before and after it is read and must not change;
    the bundled file is hashed at build time. A debug build loads its JavaScript,
    including the bundled configuration, from Metro, so its bundled hash is null and
    its `build` describes only the native binary.
  - `effectiveSettings`: every llama.rn `initLlama` parameter (except the model
    path) and `completion` parameter (except the prompt) the harness passes, for
    example `n_batch`, `n_ubatch`, `use_mmap`, `flash_attn_type`, the KV cache types,
    `temperature` and `seed`. Values equal to the binding's defaults are still passed
    explicitly; anything unlisted is the binding default of `engineVersion`. The load
    settings come from the configured run (see the table above); 0.13's own default load
    mode is `AUTO`, which the harness never uses. `distribution` records
    the binding as installed: `{source: npm, package: llama.rn, version, patches: []}`.
    xebench uses the npm package as published, with no patches, so a third party can
    rebuild it from public packages.

    **Not PocketPal's binary.** PocketPal ships llama.rn 0.13.0-rc.7 with its own
    patch (`patches/llama.rn+0.13.0-rc.7.patch`, patch-package, 1034 lines). The patch
    changes the JSI layer (`cpp/jsi/*`), the completion loop (`cpp/rn-completion.*`),
    context abort handling (`cpp/rn-llama.*`, which installs an abort callback that
    ggml checks between graph nodes), multimodal prompt bookkeeping (`cpp/rn-mtmd.hpp`)
    and stop handling in `src/index.ts`. It does not touch the vendored llama.cpp/ggml
    sources. Kernels should therefore match PocketPal's app, but TTFT and decode timing
    may differ slightly.
    cpu-reference-v2 adopts PocketPal's Android load settings (above). PocketPal's
    `n_gpu_layers: 99` with automatic devices and `kv_unified: true` are not adopted.
  - `runtimeVersions`: the versions of the runtimes the engine runs on, taken from the
    shipped artifacts, never typed by hand. For llama.cpp: `llamaCpp`
    (`{buildNumber, commit}` of the vendored llama.cpp, from llama.rn's exported
    `BuildInfo`) and `llamaRn` (the installed package version). For LiteRT-LM:
    `litertLm` and, on NPU runs, `litertNpuRuntimeQualcomm` (the AARs as Gradle
    resolved them for the APK) and `qairt` (the build string embedded in the packaged
    `libQnnHtp.so`). For GENIE-X: `genie` (the geniex-android AAR version). Runtimes the engine does not use, and
    values that are missing or malformed, are null.
  - `executionReports`: one per successful model load, with what llama.rn reported
    about that context: `gpu`, `reasonNoGPU`, `devices`, the loaded native library
    variant (`androidLib`) and llama.cpp's `systemInfo` line (threads in use and
    compiled CPU features), plus `nativeLibraries`: each `librnllama*.so` mapped in
    the process with its ELF GNU build ID, read by the BuildInfo module. llama.rn
    0.13.0-rc.7 compiles its native core from source in every app, so the build ID,
    not the npm version, names the binary. Values that are missing or out of bounds
    are null.
    These are engine statements, not verified execution, so `observedBackend`
    stays null.
  - `artifact.expectedSha256`: the run's configured `modelSha256`, or null.
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
measurement warms the OS file cache. Actual backend execution, peak memory and
quality remain unverified; engine reports and build identity are recorded as
reported, not attested. Condition sampling is 1 Hz, not continuous.

Completed sessions can be previewed from raw records with `aggregate.mjs --pretty`.
They are published only from their sanitized [evidence export](#evidence-export):
`aggregate.mjs --from-bundles evidence/ --dry-run` reports what each hosted bundle would
publish, and `--update --session <id>` publishes a named session under
[publication policy v1](RUN-CONDITIONS.md#publication-policy-v1) (README,
"Results are published here"). Raw records and diagnostics stay local; only a sanitized
evidence export may leave the machine. Historical data is unchanged.

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

`cpu-reference-v3` records (harness `xebench-0.7-reference`; earlier v2 output is
frozen in `tests/fixtures/raw-session/app-emitted-v2.json`) follow every v2 rule
and also require `build`, `configurationSource`, `effectiveSettings`,
`executionReports` and `artifact.expectedSha256`, each with a closed shape. The
validator also checks that:

- `expectedSha256` equals the configured `modelSha256` (or is null without one),
  and that `stopReason` is `model-hash-mismatch` exactly when the hashed model
  differs. Such a run has no attempts but keeps its artifact;
- the effective `n_ctx`, `n_threads` and `n_predict` agree with the protocol, and
  `n_gpu_layers` is 0;
- the effective `use_mmap`, `use_mlock`, `no_extra_bufts` and `flash_attn_type` equal the
  configured run's load settings, or the harness defaults for those it does not set.
  Records from builds that did not pass `no_extra_bufts` omit it; that is accepted only
  for a run that does not set `noExtraBufts`;
- every successful load has exactly one execution report, in repetition order, and
  a failed load has none. A report of GPU devices is kept as evidence, not
  rejected: in the `experimental` flavor llama.rn registers OpenCL even with
  `n_gpu_layers` 0;
- a staged configuration carries its hash, a bundled hash comes only from a
  reported release build, an unavailable build reports no other field, and a
  `versionName` flavor suffix matches `flavor`.

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
  in place of the prompt text, engine and version, model SHA-256 and the configured
  expectation, the configuration source and its hash, the effective engine settings,
  build identity (commit, which links bundles built from one unpublished commit;
  dirty flag, flavor, build type, a version name of the
  form `1.0-reference`, version code, and `signingKey`: `repository-debug-key` or
  `other`), execution reports (GPU flag, and llama.rn's no-GPU reason, device names
  and library variant from closed lists; `systemInfo` only as parsed thread counts
  and known feature flags), every repetition
  (metrics, token counts, methods, failure phase), condition snapshots including
  battery temperature, policies, verdicts and summaries. Device identity is
  limited to manufacturer, model, SoC and Android release. A capture contributes
  its status, counts, rejection reason-code counts, and APK names with their SHA-256.
- **Never exported:** serials, ANDROID_ID, the `ro.product.device` codename,
  install and host paths, usernames, logcat, error logs, error and cleanup
  messages, invalid payloads and their reason files, signing-certificate
  fingerprints, the raw `systemInfo` line, and `publicationReady`.

The bundle `<bundleSha256>/` holds `manifest.json`, `captures/<captureId>.json` and
`sessions/<runId>.json`. Every file is canonical JSON: sorted keys, `,`/`:`
separators, ASCII only, no NaN or Infinity, and one trailing LF. The files contain
no export time, host or path, so the same captures always give the same bytes.
**`bundleSha256` is the SHA-256 of the bytes of `manifest.json`.** The manifest
lists every other file with its SHA-256 and size. The export prints
`{sessionIds, bundleSha256}`, plus `url` when `--base-url https://...` is given,
for a verified row's `evidence`. To check a bundle, hash the bytes as served;
never re-serialize them.

Public bundles are committed byte for byte under
[`evidence/<bundleSha256>/`](../evidence/README.md) and served from
`https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence/<bundleSha256>/manifest.json`.
Exporting with `--base-url https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence`
prints that `url`. A committed bundle is never edited; a new export is a new
directory. Git does not normalise these files, and CI checks that each directory
name equals the SHA-256 of its `manifest.json` and that `verify` passes.

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
for publication: publication policy v1 decides that from the bundle.
