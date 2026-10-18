# xebench — cross-engine on-device LLM benchmark harness

**Which inference engine is actually fastest on which phone?** xebench runs the *same model, same prompt, same device, same protocol* across multiple on-device LLM engines and backends, so the numbers are comparable instead of scattered across each vendor's own README with its own quant and its own device.

It powers the cross-engine leaderboard at **[pocketpal.dev/leaderboard/engines](https://pocketpal.dev/leaderboard/engines)**.

> **Read [METHODOLOGY.md](./METHODOLOGY.md) first** — it describes the fairness contract and the current implementation status.
> **Read [ENGINES.md](./ENGINES.md)** — why each engine exists, where it shines, and whether we actually measure it there.

## Engines & coverage

| Engine | Via | CPU | GPU | NPU |
|---|---|---|---|---|
| **llama.cpp** | `llama.rn` | ✅ | ✅ OpenCL (Adreno) | Hexagon (`ggml-hexagon`): wrong output with llama.rn 0.12.4 (row marked failed); correct for Llama 3.2 1B and Gemma 3 1B with 0.13.0-rc.7 on the F8 Ultra; not yet re-measured |
| **ExecuTorch** | `react-native-executorch` | ✅ XNNPACK | — | — (delegate not in RN binding) |
| **LiteRT-LM** | native Kotlin bridge | ✅ | ⚠️ ML Drift: measured in experiment, not yet on the board (needs `uses-native-library`) | ⚠️ measured in experiment, not yet on the board; SM8550 bundle output wrong ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)) |
| **GENIE-X / Genie** | `geniex-android` / QAIRT `genie-t2t-run` | ✅ | — | ✅ Hexagon (QNN context binary) |

LiteRT-LM GPU and NPU are measured in the [accelerator experiment](./docs/experiments/litert-accelerators.md) (F8 Ultra, S23) but not yet published as board rows. See [ENGINES.md](./ENGINES.md#litert-lm-google-ai-edge).

## Headline findings so far

- **There is no single "fastest engine."** The ranking flips by model *and* by metric: LiteRT wins Gemma prefill; llama.cpp wins Qwen decode; ExecuTorch wins Llama prefill; llama.cpp wins Llama decode.
- **NPU is not automatically faster.** On a Snapdragon 8 Elite Gen 5 (Hexagon v81), for Llama 3.2 1B, Qualcomm's Genie on the NPU *wins prefill outright* but *loses decode badly* to the CPU — the Oryon cores are strong and decode is memory-bandwidth-bound.
- **Same NPU, very different integration effort for the two "NPU-first" engines.** Qualcomm's Genie ships as a complete stack: give it a device+version-matched context binary and it runs end-to-end. LiteRT-LM's Qualcomm NPU path also runs for a third party, but only after you assemble four pieces by hand: the AAR, Google's `litert-npu-runtime-qualcomm` from Maven, a QAIRT version matched to the release's `LITERT_REF`, and manifest `<uses-native-library>` declarations. A mismatch fails with opaque errors. Separately, Google's published SM8550 Gemma 3 1B bundle gives wrong output past one 128-token prefill chunk ([LiteRT-LM#3508](https://github.com/google-ai-edge/LiteRT-LM/issues/3508)). See ENGINES.md. (Corrected 2026-10-17; see [docs/CORRECTIONS.md](./docs/CORRECTIONS.md).)

## Architecture

```
xebenchapp/App.tsx ─ auto-runs every configured engine ─► EngineAdapter
                                                          ├─ LlamaRnAdapter       (llama.rn)
                                                          ├─ ExecutorchAdapter    (react-native-executorch)
                                                          ├─ LiteRtAdapter        (native LiteRtBenchModule.kt)
                                                          └─ GenieXAdapter        (native GenieXBenchModule.kt)
                                          │  logs XEBENCH_RESULT <json> per engine
                                          ▼
   scripts/capture_app_run.sh  ─ scrapes logcat, enriches device info ─► results/raw-*.jsonl
                                          ▼
   scripts/aggregate.mjs --update  ─ raw JSONL → upsert into ─► data/benchmarks.json  (canonical, published)
                                                                       │  loaded dynamically by
                                                                       ▼  pocketpal.dev/leaderboard/engines
```

It's a **React Native app** (not adb-shell binaries) on purpose: on non-rooted Android, adb-shell processes run in the throttled `background` cpuset and report numbers ~5–10× low. A foreground installed app gets the `top-app` cpuset and real clocks. See METHODOLOGY Rule 0.

Every published number traces back to a raw JSONL line carrying device, SoC, thermal state, and engine version.

## Quickstart

The default app now runs the configurable llama.cpp CPU reference path. See
[reference runs](docs/REFERENCE-RUNS.md) for configuration, per-repetition evidence
and current limitations. Other engine adapters are retained for subsequent work.

```bash
cd xebenchapp
npm ci                            # Node 20+; keep install scripts (see Build flavors)

# build, check + install the CPU reference RELEASE build (debug is unfair — see METHODOLOGY)
cd android && ./gradlew assembleReferenceRelease
python3 ../../scripts/check_reference_apk.py app/build/outputs/apk/reference/release/app-reference-release.apk
adb -s <serial> install -r app/build/outputs/apk/reference/release/app-reference-release.apk

# stage the pinned reference GGUF in the app's files dir; the script checks the SHA-256 on the host and on the device
# (download it first after accepting the Llama 3.2 license; see MODELS.md)
python3 ../../scripts/stage_model.py --serial <serial>

# run: the app auto-runs its configuration and emits evidence references
../../scripts/capture_app_run.sh <serial>       # writes a unique results/<capture-id>/ directory

# Preview a selected capture; review evidence before publishing through a PR.
node ../../scripts/aggregate.mjs ../../results/<capture-id>/raw-*.jsonl --pretty
```

Capture creates a unique `results/<capture-id>/` directory for each invocation,
with one immutable `raw-<record-id>.jsonl` file per result and a `manifest.json`
containing checksums and terminal status. Repeated runs and different
quantizations cannot overwrite earlier captures. Exit codes are 0 for a complete
capture, 2 for setup/ADB failure, 3 for timeout, 4 for partial completion (engine
errors, malformed records or no results), 130 for SIGINT and 143 for SIGTERM. Partial
records remain available for diagnosis. The deadline includes device setup and
APK fingerprint probes. A detected APK change produces `app-changed` (exit 4);
the manifest retains both fingerprints. Missing identity stays unverified.

These files are **local diagnostics**, not sanitized public evidence. UUID
filenames omit the ADB serial, but arbitrary payload fields may still contain
private information. Keep `results/` ignored and review before sharing.
`publicationReady: false` in the capture manifest is intentional: successful
transport does not establish benchmark validity. Aggregation does not yet
enforce manifest status; inspect the manifest and protocol evidence before
publishing. The captured xebench app
is stopped on completion, timeout or interruption, with up to five additional
seconds allowed for cleanup. A cleanup failure returns exit code 2.
Cancellation finishes any record write and waits for the current ADB call
(bounded to 15 seconds) before cleanup, keeping files and the manifest consistent.
If the app exits during launch, capture uses a new Android process-start event
to recover its PID and remaining records/errors; this remains a failed capture
(exit 2). If process identity cannot be recovered, it fails without reading
unattributed app logs. Error messages stay in local `error-*.txt` files; recovered
startup failures also retain `startup-logcat.txt` for native/Java errors emitted
before JavaScript starts. These diagnostic files must not be published.

## Build flavors and prerequisites

Native runtimes that cannot share one APK are Gradle product flavors. All flavors
keep the application ID `com.xebenchapp`, so installing one replaces another.

| Flavor | Gradle task | Native engines packaged | Needs files outside the repo |
|---|---|---|---|
| `reference` | `assembleReferenceRelease` | llama.rn CPU only | No — builds from a clean checkout |
| `experimental` | `assembleExperimentalRelease` | llama.rn CPU + Hexagon/OpenCL, LiteRT-LM, ExecuTorch | Only for LiteRT NPU: QAIRT libs in `app/src/experimental/jniLibs/arm64-v8a/` |
| `geniex` | `assembleGeniexRelease` | llama.rn CPU + GENIE-X (`geniex-android`, which bundles its own QNN and llama.cpp Hexagon runtime) | For the NPU path: Genie context binaries (model files) |

Prerequisites for a clean-checkout reference build (other flavors add only the
files in the table). This list is the single source; CI
([`android-build.yml`](.github/workflows/android-build.yml)) installs exactly these:

| Input | Version | Notes |
|---|---|---|
| Node | 20+ (CI: 22) | `npm ci` in `xebenchapp` **with install scripts enabled**: llama.rn downloads checksum-verified native libraries then. After `npm ci --ignore-scripts`, run `npm rebuild llama.rn --foreground-scripts` before building an APK. |
| JDK | 17+ (CI: Temurin 21) | Byte-identical APKs need the same JDK major version. |
| Gradle | 9.0.0 | Pinned by the wrapper, with `distributionSha256Sum`; use `./gradlew`. |
| Android SDK | platform `android-36`, build-tools `36.0.0`, CMake `3.22.1` | Set `sdk.dir` in `android/local.properties` or `ANDROID_HOME`. |
| Android NDK | `27.3.13750724` and `27.0.12077973` | The app's pin (the same as PocketPal; llama.rn 0.13 compiles its native core with it), and AGP's default (used by react-native-executorch). |
| Network | Maven Central, Google Maven, npm | Gradle and npm dependencies. |

`sdkmanager "platforms;android-36" "build-tools;36.0.0" "cmake;3.22.1" "ndk;27.3.13750724" "ndk;27.0.12077973"`
installs the SDK parts.

**Pinned build inputs.** npm packages are pinned by `xebenchapp/package-lock.json`.
Gradle uses no dynamic versions: `jscFlavor` is pinned to `2026004.0.1`. Every
resolved Maven dependency is locked in `android/app/gradle.lockfile` (strict mode:
an unlocked configuration fails), `android/buildscript-gradle.lockfile` and
`android/settings-gradle.lockfile`.
[Dependency verification](https://docs.gradle.org/current/userguide/dependency_verification.html)
checks every downloaded artifact against the SHA-256 in
`android/gradle/verification-metadata.xml`. A build that resolves a different version
or different bytes fails. After an intended dependency change (for example a
`package.json` or `build.gradle` bump), regenerate both from `xebenchapp/android`,
then review and commit the diff:

```sh
./gradlew --write-locks :app:dependencies            # all flavors' configurations
# an empty Gradle home, so artifacts a fresh machine downloads are all listed
GRADLE_USER_HOME="$(mktemp -d)" ./gradlew --write-verification-metadata sha256 help assembleReferenceRelease
```

The metadata is generated on Linux. AGP's `aapt2` is a per-OS artifact; the macOS
and Windows jars of the pinned version are listed by hand from Google Maven. Keep
them when regenerating, or add the new version's.

Dependencies of React Native libraries in `node_modules` reach the app through its
locked runtime classpath; their own compile classpaths are pinned only by
`package-lock.json`.

**Build identity.** Every APK embeds its git commit, a dirty flag (tracked or
untracked, non-ignored changes at build time), its flavor, build type and
`versionName` in `BuildConfig`, plus the SHA-256 of the bundled configuration.
Each session records these with the SHA-256 of the APK signing certificate (see
[reference runs](docs/REFERENCE-RUNS.md#build-identity-and-reproducibility)).
Outside a git checkout the commit and dirty flag are recorded as unknown.

`./gradlew assembleRelease` builds every flavor. Each APK lands in
`app/build/outputs/apk/<flavor>/release/app-<flavor>-release.apk`. A successful
Gradle build does not prove the runtime libraries were packaged; check each APK with
`python3 scripts/check_reference_apk.py [--flavor <flavor>] <apk>`. It requires the
flavor's libraries (for `reference`, every llama.rn CPU variant) and engine bridges,
and rejects runtimes that belong to another flavor. `reference` allows no native
library outside React Native core and llama.rn CPU, so Hexagon/HTP, OpenCL,
QNN/QAIRT, LiteRT-LM, GENIE-X and ExecuTorch libraries all fail the check.

Known limitations: React Native autolinking is not flavor-aware, so
react-native-executorch's Java glue is linked into every flavor; `reference` and
`geniex` exclude its native libraries, and its module reports itself unavailable if
called. On the S23, llama.rn's Hexagon/OpenCL library hangs at first model load;
build `experimental` with `-Pxebench.excludeLlamaHexagon=true` to use its CPU
libraries there.

## Native dependencies (you must obtain these yourself)

This repo does **not** vendor model weights or proprietary vendor SDKs.

- **Models** — GGUF / `.pte` / `.litertlm` / QNN context binaries. See **[MODELS.md](./MODELS.md)** for exact files and sources.
- **QAIRT SDK** (Qualcomm AI Runtime) — needed for the LiteRT-NPU path and the CLI `genie-t2t-run` Genie measurements. Download the *Community* track (direct download) from Qualcomm, then stage the libraries LiteRT NPU needs with `python3 scripts/stage_qairt_libs.py --qairt <QAIRT root>`. It copies the `lib/aarch64-android/` host libs and the v73/v81 `libQnnHtpVNNSkel.so` into `app/src/experimental/jniLibs/arm64-v8a/` (git-ignored — Qualcomm-licensed, do not redistribute), refuses a destination git would track, and records each file's SHA-256 in `app/src/experimental/jniLibs/qairt-libs.json`. Check an NPU-capable APK with `scripts/check_reference_apk.py --flavor experimental --require-qnn <apk>`. Never put them in `app/src/main/jniLibs`. For **LiteRT-LM**, the QAIRT version must match the `LITERT_REF` of the LiteRT-LM release you use. The dispatch lib (`libLiteRtDispatch_Qualcomm.so`) is not part of QAIRT: it comes from Google's `com.google.ai.edge.litert:litert-npu-runtime-qualcomm` Maven artifact, or from the per-arch `litert_npu_runtime_libraries.zip` on LiteRT GitHub releases. The `experimental` flavor pins `litertlm-android` 0.18.0 + `litert-npu-runtime-qualcomm` 2.3.0, validated with QAIRT 2.50.0.260828 (see [docs/experiments/litert-accelerators.md](./docs/experiments/litert-accelerators.md)).
- **GENIE-X** (`com.qualcomm.qti:geniex-android`) — Maven Central dependency of the `geniex` flavor only.
- **Genie context binaries** — per-SoC, per-QAIRT-version compiled `.bin` bundles (e.g. from `runanywhere/genie-npu-models` or Qualcomm AI Hub). They must match *both* your device's Hexagon arch (v73/v75/v79/v81) *and* a QAIRT version you can obtain.

### Toolchain gotchas (hard-won)
- **Node 20+** (RN 0.82 Metro).
- Host is often aarch64 Linux — point `hermesCommand` at the x86-64 hermesc (runs via emulation).
- **Kotlin 2.3.0** required by `litertlm-android` (pin `kotlinVersion` *and* the plugin classpath).
- `litertlm-android` needs `useLegacyPackaging` / `extractNativeLibs=true` so QNN libs land in `nativeLibraryDir`.
- LiteRT-LM **GPU** (ML Drift) needs `<uses-native-library android:name="libOpenCL.so" android:required="false" />` and the same for `libvndksupport.so` in the app manifest, as the LiteRT-LM Kotlin guide has said since 0.14.0 ([LiteRT-LM#1860](https://github.com/google-ai-edge/LiteRT-LM/issues/1860)). Without them OpenCL does not load (`libvndksupport.so` not found): on the F8 Ultra the engine then fails with `Can not find OpenCL library on this device`, and on the S23 ML Drift falls back to WebGPU and engine creation fails. The `experimental` flavor manifest (`app/src/experimental/AndroidManifest.xml`) declares both.
- **Runtimes don't coexist:** llama.rn's Hexagon lib and GENIE-X's bundled Hexagon runtime collide, and llama.rn hijacks `ADSP_LIBRARY_PATH`. They are separate [build flavors](#build-flavors-and-prerequisites).

## Results are published here — the website loads them dynamically

**[`data/benchmarks.json`](./data/benchmarks.json) is the canonical, published results file** and the single source of truth. It's a versioned envelope (`schemaVersion`, `generatedAt`, `source`, `rows[]`) validated by **[`data/benchmarks.schema.json`](./data/benchmarks.schema.json)**. Each row carries its `provenance` (`measured` = our lab runs · `vendor` = engine-maker official numbers), `quant`, source link, and methodology caveats (`asStated`).

The current contract is **1.1** (additive over 1.0; see the [website handoff](docs/WEBSITE-HANDOFF.md#agreed-contract-11) for the field table). `generatedAt` is ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`; the 1.0 date-only form stays valid). Measured rows may carry `validation` (`legacy` · `unverified` · `failed` · `verified`), `guardsPassed`, `conditionPolicy`, `protocolVersion`, `timingMethod`, `dispersion`, `evidence` and `build`; vendor rows never do. Provenance says who supplied a number; `validation` says what was checked. Every existing measured row is `legacy`. Nothing is `verified` yet: that needs an evidence pipeline.

**Publish reviewed measurements through a PR — no website change.** Only legacy records (no `schemaVersion`) can currently be published, only with `--allow-legacy`, and always as `validation: "legacy"`. Sessions captured from the current app (`schemaVersion` 1) are preview-only ([#4](https://github.com/a-ghorbani/xebench/issues/4)); their preview rows are `unverified` or `failed`.

```bash
git switch -c results/reviewed-measurements
node scripts/aggregate.mjs <reviewed-legacy-raw>.jsonl --update --allow-legacy
git add data/benchmarks.json
git commit -m "results: add reviewed measurements"
git push -u origin results/reviewed-measurements
# Open a PR targeting main. Publication happens after review and merge.
```

`--update` reads `data/benchmarks.json`, upserts the freshly-measured rows (keyed by engine·backend·platform·device·model·quant·provenance, so re-runs overwrite in place), re-stamps `generatedAt` (ISO-8601 UTC), and writes it back as contract 1.1. Existing rows (other devices, vendor citations) are preserved. It requires a file already migrated to 1.1.

Publication fails closed: on any error it exits 1 and leaves `data/benchmarks.json` byte-identical.

- The existing file and the result must match the schema, with unique row keys and a correct `rowCount`. The file is replaced atomically (temp file + rename).
- Every input line must be a finite, complete `xebench-raw` record. Malformed, truncated or non-finite lines, unknown schema/protocol versions and empty input reject the whole run (also in preview).
- Legacy records are published only with the explicit `--allow-legacy` flag, are logged as `LEGACY` and get `validation: "legacy"`.
- Contract invariants are enforced on every emitted row and in validation: `verified` requires `guardsPassed: true` and `evidence`; `guardsPassed: false` requires `validation: "failed"`; legacy rows carry no `dispersion` or `evidence`; 1.1 fields appear on measured rows only. Aggregation refuses to emit `verified` (including a `verified` row already in the canonical file) until an evidence pipeline exists.
- Failed or unverified guards and incomplete sessions are rejected. `sampleCount` is the summary `n`, checked against the successful repetitions in the record. Two legacy producers never exported repetitions: the old app (`xebench-0.4-app`) and `bench_llamacpp.py` (`xebench-0.2-native`). Their self-reported `n` is accepted and logged; any other record without repetitions is rejected.
- Two input records with the same row key are rejected rather than picking one implicitly; a single input row replacing an existing row is logged.
- An existing row marked `validation: "failed"` (for example, invalidated by a later output-correctness re-check) is never overwritten or downgraded, even with `--allow-legacy`; the run is refused. Replacing one needs the explicit `--replace-failed` flag, and each replacement is logged.
- Rows are ordered by platform, device, model, engine, backend, quant and provenance (plain string comparison), independent of input order and locale. The first update after this change swaps three pairs of existing rows that tied on the first four fields.

`node scripts/validate_data.mjs` checks the checked-in `data/*.json` against their schemas and contract invariants; `node scripts/validate_data.mjs <file>...` checks any benchmarks envelope. CI runs it with `tests/test_publication.py` and `tests/test_contract_publication.py`.

**Contract migrations are explicit scripts.** `node scripts/migrate_contract_1_1.mjs` migrates a 1.0 file to 1.1: it sets `schemaVersion` "1.1", re-stamps `generatedAt` and appends `validation: "legacy"` to measured rows without one. No other field changes and vendor rows stay byte-identical. It fails closed like `--update` and is idempotent; `--check` exits 1 when a migration is still needed (CI runs it). Website consumer fixtures for 1.1 are in [`tests/fixtures/contract-1.1/`](tests/fixtures/contract-1.1/).

**Rollout rule: the consumer is deployed before the producer merges.** A contract change to `data/benchmarks.json` (a new minor or major) merges only after the pocketpal.dev consumer that reads it is deployed.

**Consumers load this file dynamically** — the pocketpal.dev leaderboard fetches it from the repo (raw URL / CDN) with periodic revalidation and overlays live crowd data, so a commit here surfaces on the board with no redeploy or dashboard-dev work. The stable interface is the schema; additive changes bump the minor, breaking changes bump the major, and consumers reject a major they do not support. (`--pretty` without `--update` still just prints `EngineBenchmarkRow[]` to stdout for ad-hoc use.)

**Narrative findings are data too.** [`data/findings.json`](./data/findings.json) (schema: [`data/findings.schema.json`](./data/findings.schema.json)) publishes the lab’s written findings — headline, claim strings, a pass/fail step strip, sources, `verifiedAt` — so consumers render the lab’s wording **verbatim** and never re-word a claim. Same rule as the numbers: the benchmark owns the claim, the site renders it.

## Contributing

Good first issues:
- Add a **quality metric** (perplexity or a small task-accuracy set) so the board isn't speed-only — the top methodology gap (see METHODOLOGY §quant).
- New device rows (MediaTek / more Snapdragons), new engines (MLX/CoreML need an iOS harness).
- A Rule-0-clean, in-app Genie NPU path (currently CLI-measured).

Please keep every number traceable to a raw JSONL record and label its provenance and quant honestly — that transparency is the whole point.

## License

[Apache-2.0](./LICENSE). Note that model weights and vendor SDKs referenced here carry their **own** licenses (Qualcomm QAIRT, Meta Llama, Google Gemma, etc.) — obtain and use them under those terms.
