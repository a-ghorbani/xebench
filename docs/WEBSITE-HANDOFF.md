# Website handoff

Proposed PocketPal leaderboard changes for
[publication issue #4](https://github.com/a-ghorbani/xebench/issues/4).
This is a handoff, not an implementation or deployment report.

Integration points: `lib/engines/data.ts` (fetch/fallback),
`lib/engines/types.ts` (row types), and
`components/leaderboard/EnginesExplorer.tsx` (comparisons/evidence).

## Agreed contract 1.1

Agreed by the website implementer and the PM. Contract 1.1 is additive: every
1.0 file still validates against
[`data/benchmarks.schema.json`](../data/benchmarks.schema.json), and the meaning
of every 1.0 field is unchanged. On measured rows, `ppTps`/`tgTps` are medians
of `sampleCount` successful repetitions.

**Rollout rule: the consumer is deployed before the producer merges.** The
website must accept 1.1 (and keep accepting 1.0) in production before the
xebench PR that publishes `schemaVersion` "1.1" is merged. The PM gates that
merge.

Envelope:

| Field | Type | Meaning | Invariants |
| --- | --- | --- | --- |
| `schemaVersion` | string | Contract version, `"1.1"` | `MAJOR.MINOR`. Reject the whole file unless the major is `1`; keep the compatible fallback. A `"1.0"` file carries no 1.1 row fields. |
| `generatedAt` | string | UTC time the file was last regenerated | `YYYY-MM-DDTHH:MM:SSZ` from 1.1; the 1.0 date-only `YYYY-MM-DD` stays valid. A real calendar date/time. |

New optional row fields. They are valid on `provenance: "measured"` rows only;
vendor and community rows never carry them.

| Field | Type | Meaning | Invariants |
| --- | --- | --- | --- |
| `validation` | `"legacy"` \| `"unverified"` \| `"failed"` \| `"verified"` | Validation status, separate from provenance | `verified` requires `guardsPassed: true` and `evidence`. Absent on a measured row means legacy (1.0 files), with the same legacy rules. |
| `guardsPassed` | boolean \| null | Run-condition guard outcome: true passed, false failed, null not verified | `false` requires `validation: "failed"`. Null is not a pass. |
| `conditionPolicy` | string | Run-condition policy id, e.g. `observed-conditions-v1` | none |
| `protocolVersion` | string | Measurement protocol id, e.g. `cpu-reference-v1` | none |
| `timingMethod` | string | How `ppTps`/`tgTps` were timed, e.g. `engine-timings` | none |
| `dispersion` | `{ ppTps?: {iqr, min, max}, tgTps?: {iqr, min, max} }` | Spread of the repetitions behind each median, tokens/s | Finite, non-negative numbers; `min <= max`; all three keys in each present metric. Absent on legacy rows. |
| `evidence` | `{ sessionIds, bundleSha256, url }` | Immutable sanitized evidence behind the row | `sessionIds`: non-empty array of unique non-empty strings; `bundleSha256`: 64-character lowercase hex; `url`: `https://` URL with a host, no credentials, printable ASCII only. Required when verified; absent on legacy rows. |
| `build` | `{ engineVersion?, bindingVersion?, apkSha256?, harnessCommit? }` | Identity of the producing software | All values strings; no other keys. |

There is no `eligibility` field in 1.1 or 1.2; it is rejected until a later
minor defines it. Unknown row fields are rejected (`additionalProperties: false`), so
a consumer that validates with this schema must also be updated before the
producer publishes a later minor's fields.

The schema expresses every invariant above except `min <= max`, calendar
validity of `generatedAt` and the 1.0-files rule; it was cross-checked with Ajv
(2020-12, strict, ajv-formats). `scripts/lib/publication.mjs` enforces all of them; `node scripts/validate_data.mjs <file>` checks any
envelope. The producer (`scripts/aggregate.mjs`) labels rows from legacy records
`validation: "legacy"`. It emits `verified`, `unverified` and `failed` rows only from
hosted evidence bundles, under publication policy v1 (see
[Published sessions and row selection](#published-sessions-and-row-selection)).
Since measurement sets ([below](#measurement-sets-what-the-board-renders)),
`data/benchmarks.json` holds only rows the board displays: vendor rows, July 2026 rows without
versions (`validation: "legacy"`) and rows published from evidence bundles. It never holds a
`failed` row; failed and replaced rows are in `data/sessions/index.json`.

Migration: `node scripts/migrate_contract_1_1.mjs` set `schemaVersion` "1.1",
re-stamped `generatedAt`, and appended `validation: "legacy"` to each of the 26
measured rows. Nothing else changed; the 15 vendor rows are byte-identical. The
migration is idempotent, and `--check` verifies that it is a no-op.

Consumer fixtures are in
[`tests/fixtures/contract-1.1/`](../tests/fixtures/contract-1.1/); `index.json`
lists each file and whether a consumer must accept or reject it:

| Fixture | Expect | Covers |
| --- | --- | --- |
| `legacy.json` | accept | Migrated lab row |
| `verified.json` | accept | Synthetic row with every 1.1 field (evidence URL does not resolve) |
| `failed-condition.json` | accept | Synthetic row, `guardsPassed: false`, dispersion without evidence |
| `unverified.json` | accept | Synthetic row, `guardsPassed: null` |
| `vendor.json` | accept | Vendor claim without 1.1 fields |
| `v1.0-date-only.json` | accept | Pre-migration 1.0 file with date-only `generatedAt` |
| `unsupported-major.json` | reject | `schemaVersion` "2.0" |

## Agreed contract 1.2

Agreed by the website implementer and the PM. Contract 1.2 is additive over 1.1:
every 1.0 and 1.1 file still validates, and no 1.1 field changes meaning. It adds
optional fields on measured rows only: `build.runtimes`, which names each software
component behind a row with its version, and `configurationId`/`configurationSha256`,
which name the run configuration behind a row published from an evidence bundle. A
file declares `schemaVersion` "1.2" only when a row carries one of them. The same
rollout rule applies: the website accepts 1.2 in production before the producer
publishes a 1.2 file.

| Field | Type | Meaning | Invariants |
| --- | --- | --- | --- |
| `build.runtimes` | `[{ name, version }]` | Each component that ran, with its version | Measured rows only, in `schemaVersion` "1.2" files only. Non-empty; one entry per `name`. `name` is one of `llama.cpp`, `llama.rn`, `LiteRT-LM`, `litert-npu-runtime-qualcomm`, `QAIRT`, `geniex-android`. `geniex-android` is the GENIE-X AAR (`com.qualcomm.qti:geniex-android`, e.g. `0.3.1`), not Qualcomm's Genie runtime; a Genie runtime version, if recorded later, gets its own name. `version` is a llama.cpp build (`b11385`) or a dotted version with an optional pre-release (`0.13.0-rc.7`, `2.50.0.260828221209`): `^(b[0-9]{1,7}\|[0-9]{1,5}(\.[0-9]{1,12}){1,3}(-[0-9A-Za-z]{1,16}(\.[0-9A-Za-z]{1,16}){0,3})?)$`, at most 64 characters. No other keys. |
| `build.engineVersion` | string | The engine's own version | With `runtimes`: `llama.cpp b<build> (<7-hex commit>)`, `LiteRT-LM <version>` or `geniex-android <version>`. Without `runtimes`, any string, as in 1.1. |
| `build.bindingVersion` | string | The app binding the engine runs through | With `runtimes`: `llama.rn <version>`. Without `runtimes`, any string, as in 1.1. |
| `configurationId` | string | The id of the run configuration the session ran, e.g. `cpu-reference-v2` | Top-level, next to `protocolVersion`. Measured rows from evidence bundles only (requires `evidence`); never on legacy or vendor rows; in `schemaVersion` "1.2" files only. `^[a-z0-9][a-z0-9.-]{0,63}$`. |
| `configurationSha256` | string | SHA-256 of that configuration file's bytes, as the session recorded it | Top-level. Only with `configurationId`; 64 lowercase hex. Absent when the session recorded no hash. |

Example (a llama.cpp row):

```json
"build": {
  "engineVersion": "llama.cpp b11385 (bf9a0cc)",
  "bindingVersion": "llama.rn 0.13.0-rc.7",
  "runtimes": [{"name": "llama.cpp", "version": "b11385"}, {"name": "llama.rn", "version": "0.13.0-rc.7"}]
}
```

A LiteRT-LM NPU row lists `LiteRT-LM`, `litert-npu-runtime-qualcomm` and
`QAIRT` and has no `bindingVersion`. The schema expresses every 1.2 invariant
except one entry per `name` (identical duplicate entries fail its
`uniqueItems`); `scripts/lib/publication.mjs` enforces all of them. The producer
fills `build` from the versions a session records (`runtimeVersions` in the raw
session); a component the session does not report is absent, never guessed. It fills
`configurationId` and `configurationSha256` only when the session's `configurationSource`
hash names a committed configuration file and the session carries exactly that file's
content: the id is that file's id, and the hash is the SHA-256 of its bytes. Otherwise a row
names no configuration, and the board never displays it. The website is to show `configurationId` as the row's
"Config" in its details, and must accept both fields in production before a file that
carries them is published.

Producer rules and consumer behavior are separate. The producer rules above are
strict: `scripts/validate_data.mjs` refuses a file that breaks one, so xebench
never publishes it. Every version the producer can emit also passes the
website's version-token check `^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$`. The website
degrades per entry or per row instead of refusing the file. It falls back to the
whole file only for an unknown major version, malformed JSON or a file with no
valid rows.

Consumer fixtures are in
[`tests/fixtures/contract-1.2/`](../tests/fixtures/contract-1.2/), listed in
`index.json`. Each entry states `producerMustNotEmit` (whether the file breaks
a producer rule) and `consumerExpectation` (what the website does with it):

| Fixture | Producer must not emit | Consumer expectation | Covers |
| --- | --- | --- | --- |
| `llama-cpp-runtimes.json` | false | Rendered: engine, binding and runtime versions shown | Synthetic llama.cpp row: engine build, llama.rn binding, both as runtimes |
| `litert-npu-runtimes.json` | false | Rendered: engine and runtime versions shown; no binding | Synthetic LiteRT-LM NPU row: LiteRT-LM, Qualcomm NPU dispatch runtime and QAIRT |
| `v1.1-rows-in-1.2.json` | false | Rendered unchanged | A 1.2 file whose rows carry no 1.2 field |
| `runtimes-in-1.1.json` | true | Rendered; the runtimes are harmless | `build.runtimes` in a file that declares 1.1 |
| `duplicate-runtime.json` | true | That runtime's entries are dropped; the rest of the row renders | Two entries with the same `name` and conflicting versions |
| `unknown-runtime-name.json` | true | Shown | A `name` outside the closed list |
| `bad-runtime-version.json` | true | That entry is dropped by the version-token check | A free-text `version` (`0.18.0 (Maven)`) |
| `engine-version-form.json` | true | Shown sanitised | With `runtimes`, an `engineVersion` outside the fixed forms |
| `runtimes-on-vendor-row.json` | true | Ignored | `build` on a vendor row |
| `configuration.json` | false | Rendered: `configurationId` shown as the row's configuration | Synthetic bundle row with `configurationId` and `configurationSha256` |
| `configuration-in-1.1.json` | true | Rendered; the configuration fields are harmless | Configuration fields in a file that declares 1.1 |
| `configuration-on-vendor-row.json` | true | Ignored | `configurationId` on a vendor row |
| `configuration-on-legacy-row.json` | true | Ignored | `configurationId` on a legacy row |
| `configuration-without-evidence.json` | true | Rendered; the configuration is shown | `configurationId` on a measured row without `evidence` |
| `configuration-sha-without-id.json` | true | Rendered; the hash alone is not shown | `configurationSha256` without `configurationId` |
| `bad-configuration-id.json` | true | That field is dropped by the id form check; the rest of the row renders | A `configurationId` outside `^[a-z0-9][a-z0-9.-]{0,63}$` |

## Published sessions and row selection

Rows from evidence bundles use the contract 1.1/1.2 fields above; no field changes
meaning. For those rows:

- `validation` and `guardsPassed` are the
  [publication policy v1](RUN-CONDITIONS.md#publication-policy-v1) decision:
  `verified`/`true`, `unverified`/`null` or `failed`/`false`. `asStated` names each
  check that did not pass.
- `evidence.url` is the bundle's manifest,
  `https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence/<bundleSha256>/manifest.json`.
  The bundle is immutable: its directory name is the SHA-256 of `manifest.json`, and
  the manifest lists every file with its SHA-256. `evidence.sessionIds` names the one
  session behind the row, at `sessions/<sessionId>.json` next to the manifest.
- `ppTps`, `tgTps` and `ttftMs` are medians, and `dispersion` holds the IQR, min and
  max of `ppTps` and `tgTps`. All are recomputed from the session's successful
  repetitions with linear interpolation at `(n - 1) * p` and rounded to 0.1, so a
  consumer can recompute a displayed median from the linked session.
- `configurationId` and `configurationSha256` name the run configuration the session
  ran (contract 1.2), so every bundle row is in a "1.2" file.
- `memoryMb` is absent. `build.apkSha256` is present only when the capture lists one
  installed APK; any unknown build value is absent.

A row key (`engine|backend|platform|device|model|quant|provenance`) can have
several published sessions. The board shows at most one row per key, and
[`data/sessions/index.json`](../data/sessions/index.json) (schema:
[`data/sessions/index.schema.json`](../data/sessions/index.schema.json)) keeps the
history:

| Field | Meaning |
| --- | --- |
| `keys[].key` | The row key |
| `keys[].sessions[]` | Every published session for the key, oldest first: `sessionId`, `timestampIso`, `bundleSha256`, `url`, `validation`, `guardsPassed` and the full `row` it publishes |
| `keys[].displayed`, `keys[].rule` | The session `data/benchmarks.json` shows, and why: `newest-verified` or `newest-unverified`; `null` with `retired` (the key is in `configs/board.json` `retiredKeys`) or `no-session-in-set` (no displayable session in the engine's set: staged, failed or of another configuration or backend) |
| `keys[].replacedRows[]` | A row without a session that left `data/benchmarks.json`, as it was: replaced by a session (`reason` `replace-legacy` or `replace-failed`, `replacedBy`) or retired (`reason` `retired`, `note`, `retiredAt`) |

Selection is explicit and deterministic (index `schemaVersion` "1.1", `selectionRule`
`engine-set-newest-verified-else-newest-unverified`). Among the key's published sessions in its
engine's declared set ([`configs/board.json`](../configs/board.json): configuration and backend,
engine and binding version, measured from a date), it picks the newest verified session, else the
newest unverified, where newest is the latest `timestampIso`, then `sessionId`. A failed session
is never displayed, and a retired key displays nothing. Other sessions stay in the history
(published with the logged `--stage` or `--allow-unlisted-configuration`). The board row for a
key is exactly `sessions[displayed].row`, and `validate_data.mjs` refuses any file pair where it
is not, where applying `configs/board.json` would change anything, or where a session's row does
not come out of its hosted bundle again. A published session is never removed or rewritten.

The index is an additive, optional file: the board needs only
`data/benchmarks.json`. A consumer that shows history links reads the index and
treats an unknown `schemaVersion` major as absent. Fixtures with the producer's real
output, one 1.3 pair, are in
[`tests/fixtures/verified-publication/`](../tests/fixtures/verified-publication/) (the 1.2 pairs
are gone: the producer no longer writes a failed row into `rows`, and a session without a
recorded engine version is never displayed):

| Fixture | Producer must not emit | Consumer expectation |
| --- | --- | --- |
| `board-1.3.json` | false | Rendered: every row; versions shown as text on each bundle row; the July 2026 row shows its month and no version; the vendor row in the Vendor-reported section |
| `sessions-1.3.json` | false | Optional history: a replaced July row; a key whose only session failed has `displayed: null` and no board row |

## Measurement sets: what the board renders

Owner decisions of 2026-10-19 ([RUN-CONDITIONS.md, Measurement sets](RUN-CONDITIONS.md#measurement-sets)).
Proposed for the website implementer; the producer side is in this repository.

**Display approach: `rows` is the board.** The producer writes only displayed rows into
`data/benchmarks.json` `rows`. Superseded, staged, retired and failed rows, and the July rows of
an engine once it is re-measured, live only in `data/sessions/index.json`. The site needs no
selection logic and no new field: it renders every row in `rows` and nothing else. This keeps the
contract unchanged (removing rows is always valid: the site falls back to its bundled data only for
an unknown major, malformed JSON or no valid rows), and the producer refuses to write a partial
set. Each engine's rows come from one set: every llama.cpp row carries llama.cpp b11385 (bf9a0cc),
llama.rn 0.13.0-rc.7 and its month; every LiteRT-LM lab row carries LiteRT-LM 0.18.0 (and, on the
NPU row, litert-npu-runtime-qualcomm 2.3.0 and QAIRT 2.50.0.260828221209) and its month, with no
binding; and the measured ExecuTorch and GENIE-X rows are July 2026 rows (month only). A board that declares a `carryover` would show an engine's July rows next to
its new set; the row text tells them apart, and the page should then not present the two as one
comparable series. No engine declares one.

What the site renders:

| Data | Website requirement |
| --- | --- |
| Versions | On every row that has them, as text: `build.engineVersion` and `build.bindingVersion` (e.g. "llama.cpp b11385 (bf9a0cc) · llama.rn 0.13.0-rc.7"). Not in a tooltip or details panel only. |
| Rows without versions | Show the measurement month and no version. Never explain why, and never print `validation` ("legacy") or any placeholder such as "unknown version". |
| Measurement month | On every row: `sourceDate` (`YYYY-MM`) as a month, e.g. "Jul 2026", "Oct 2026". `dateBasis` (contract 1.3) is data for readers of the file; do not render it. |
| Status words | No "legacy", "failed", "retired", "superseded", "pending" or other lab-book label anywhere. `validation` is not rendered as a word; a verified row may show its evidence link. An `unverified` row needs a plain, self-explanatory label if it is marked at all. |
| Vendor rows | In a separate "Vendor-reported" section, not mixed into lab rows; keep their `asStated` (one plain sentence), `sourceName` and source link. |
| Chip | `chipName` (e.g. "Snapdragon 8 Elite Gen 5") where present; `soc` is a code for checks, not for display. |
| Source | Lab rows carry `sourceName` "xebench"; vendor rows name their source. |
| Quant | Render `quantBits` as "4-bit" / "8-bit" / "16-bit", with the exact `quant` scheme (Q4_K_M, Q4_0, W4A16, 4-bit QAT, ...) as secondary text and a glossary tooltip: Q4_K_M and Q4_0 are different setups, so the scheme stays visible. A row without `quantBits` shows its scheme alone. |
| Labels | `quant` may be absent when the source states none: the site words that itself. Labels follow one vocabulary (quant, upper-case SoC, "Brand model" devices, android/ios only), enforced by the lab-book gate. Speeds are "tok/s" in the data text. |
| Lab row notes | Do not render `asStated` on measured rows: it is lab-book provenance (policy names, prompt ids). |
| Findings | Render only `headline`, `deck` and the `detailsUrl` link, verbatim. Do not render `context` (for example `correctedAt`). The finding `llama-cpp-hexagon-output` is removed; the board does not explain the retired Hexagon row. |

Every string in this table is checked in CI by `scripts/check_public_text.mjs` (the lab-book gate,
[STYLE.md](STYLE.md#board-text-the-lab-book-gate)), and every public-facing pull request completes
the first-time visitor review on the rendered page.

### `chipName` (additive, contract 1.2)

An optional row field, valid in "1.2" and later files: the SoC's marketing name for the row's
device. Lab rows take it from [`configs/devices.json`](../configs/devices.json) (`chipName` per
device model; bundle rows get it from the producer, legacy rows from
`scripts/migrate_board_labels.mjs`). Vendor rows carry it only where the device ships with one
chip. `soc` stays the code publication policy v1 checks.

#### Chip names

| Device | `soc` | `chipName` | Source |
| --- | --- | --- | --- |
| Samsung Galaxy S23 (SM-S911B) | SM8550 | Snapdragon 8 Gen 2 for Galaxy | TENAA listing of the S23 series as SM8550-AC, the "for Galaxy" part ([Notebookcheck](https://www.notebookcheck.net/Samsung-Galaxy-S23-series-confirmed-to-launch-with-more-powerful-Snapdragon-8-Gen-2-chipset-than-Xiaomi-13-or-Xiaomi-13-Pro.674181.0.html)) |
| POCO F8 Ultra | SM8850 | Snapdragon 8 Elite Gen 5 | [GSMArena spec sheet](https://www.gsmarena.com/xiaomi_poco_f8_ultra_5g-14301.php); SM8850 = Snapdragon 8 Elite Gen 5 ([Notebookcheck](https://www.notebookcheck.net/Qualcomm-confirms-Snapdragon-8-Elite-Gen-5-moniker-for-forthcoming-flagship-SoC.1114667.0.html)) |
| Google Pixel 9 | Tensor G4 | Google Tensor G4 | Google's name for the chip |
| OnePlus 6 | SDM845 | Snapdragon 845 | [WikiChip SDM845](https://en.wikichip.org/wiki/qualcomm/snapdragon_800/845) |
| POCO X8 Pro | MT6899 | Dimensity 8500-Ultra | POCO's launch ([GSMArena](https://www.gsmarena.com/poco_confirms_the_chipsets_for_the_upcoming_x8_pro_series-news-71829.php)). Public sources also tie MT6899 to the Dimensity 8400, so the name comes from the phone, not the code |
| Google Pixel 7 Pro (vendor) | — | Google Tensor G2 | [MobileSyrup comparison](https://mobilesyrup.com/2023/10/04/heres-how-the-pixel-8-pro-compares-to-the-pixel-7-pro/) |
| Google Pixel 8 Pro (vendor) | — | Google Tensor G3 | [Trusted Reviews](https://www.trustedreviews.com/explainer/what-is-the-tensor-g3-4375901) |
| OnePlus 12 (vendor) | — | Snapdragon 8 Gen 3 | [PhoneArena comparison](https://www.phonearena.com/reviews/samsung-galaxy-s24-ultra-vs-oneplus-12_id6053) |
| Samsung Galaxy S24 Ultra (vendor) | — | Snapdragon 8 Gen 3 for Galaxy | Same in every market ([PhoneArena](https://www.phonearena.com/reviews/samsung-galaxy-s24-ultra-vs-oneplus-12_id6053), [Android Authority](https://www.androidauthority.com/best-snapdragon-8-gen-3-phones-3442576/)) |
| Apple iPhone 17 Pro (vendor) | — | Apple A19 Pro | [Apple Newsroom](https://www.apple.com/newsroom/2025/09/apple-unveils-iphone-17-pro-and-iphone-17-pro-max) |

Galaxy S22 and S24 ship with a Snapdragon or an Exynos chip depending on the market, and the S26
Ultra's chip is not verified here, so those vendor rows carry no `chipName`.

### Contract 1.3 (proposed): `dateBasis`, `quantBits`

Contract 1.3 is additive over 1.2. It adds two optional row fields: `dateBasis`, valid only on
legacy measured rows that carry `sourceDate`, and `quantBits`; a file declares "1.3" only when a
row carries one. The rollout rule
holds: the website accepts 1.3 in production before the producer pull request that publishes it
merges.

| Field | Type | Meaning | Invariants |
| --- | --- | --- | --- |
| `dateBasis` | `"measured"` \| `"first-published"` | What `sourceDate` is on a row without versions: the month the row was measured, or (only when that cannot be established) the month it was first published | Legacy measured rows only, with `sourceDate`; in `schemaVersion` "1.3" files only. Not displayed. |
| `quantBits` | `4` \| `8` \| `16` | The weight bit width the `quant` scheme names | Any row, only where the width is certain (`scripts/lib/publication.mjs` `quantBitsFor`: Q4_0, Q4_K_M, int4, W4A16, SpinQuant 4-bit, 4-bit groupwise, 4-bit QAT (+ LoRA) and `4-bit weights, 8-bit activations` are 4; Q8, Q8_0 are 8; BF16 is 16; the mixed labels `mixed 2/4/8-bit` and `mixed 4/16-bit` have none); must equal that mapping; in "1.3" files only. Displayed as "4-bit" with the scheme as secondary text. |

Consumer fixtures are in [`tests/fixtures/contract-1.3/`](../tests/fixtures/contract-1.3/),
listed in `index.json` with `producerMustNotEmit` and `consumerExpectation` as for 1.2. The
current data dates all 20 July rows (11 on the board, the 9 llama.cpp rows in the history) to
2026-07: 18 `measured`, 2 `first-published` (Galaxy S23,
Gemma 3 1B, LiteRT-LM int4 and llama.cpp Q4_0 QAT).

Their evidence URLs name the fixture bundles' real hashes but do not resolve.

### Contract 1.4 (proposed): ExecuTorch in `build`

Contract 1.4 is additive over 1.3. It adds no field. It widens three existing `build` values on
measured rows so that an ExecuTorch row can state its versions, and a file declares "1.4" only
when a row carries one of them. The rollout rule (the website accepts 1.4 in production before
the first ExecuTorch row with versions is published) is met: the website accepts 1.4 as it is.

| Field | What 1.4 adds | Invariants |
| --- | --- | --- |
| `build.runtimes[].name` | `ExecuTorch` (the ExecuTorch runtime, e.g. `1.4.1`) and `react-native-executorch` (the React Native binding, e.g. `0.10.7`) | Same as 1.2: one entry per `name`, the same `version` grammar and 64-character limit. In "1.4" files only. |
| `build.engineVersion` | With `runtimes`: `ExecuTorch <version>`, e.g. `ExecuTorch 1.4.1` | Without `runtimes`, any string, as before. |
| `build.bindingVersion` | With `runtimes`: `react-native-executorch <version>`, e.g. `react-native-executorch 0.10.7` | Without `runtimes`, any string, as before. |

Example (an ExecuTorch CPU row):

```json
"build": {
  "engineVersion": "ExecuTorch 1.4.1",
  "bindingVersion": "react-native-executorch 0.10.7",
  "runtimes": [{"name": "ExecuTorch", "version": "1.4.1"}, {"name": "react-native-executorch", "version": "0.10.7"}]
}
```

What this means for the website: no change. It keeps no closed runtime list, does not gate
fields on the minor version and does not send `build.runtimes` to the client, so it accepts
`schemaVersion` "1.4" and the two runtime names as they are. It displays the engine and binding
versions like the llama.cpp ones: engine version "ExecuTorch 1.4.1", binding
"react-native-executorch 0.10.7"; an engine version outside the fixed forms is shown sanitised.

Nothing else changes: no new row field, no change in meaning for any 1.3 field, and rows
without ExecuTorch versions are unchanged. Every 1.0 to 1.3 file still validates.

Consumer fixtures are in [`tests/fixtures/contract-1.4/`](../tests/fixtures/contract-1.4/),
listed in `index.json` with `producerMustNotEmit` and `consumerExpectation` as for 1.2:

| Fixture | Producer must not emit | Consumer expectation | Covers |
| --- | --- | --- | --- |
| `executorch-runtimes.json` | false | Rendered: engine version "ExecuTorch 1.4.1", binding "react-native-executorch 0.10.7" | Synthetic ExecuTorch CPU row with both runtimes |
| `v1.3-rows-in-1.4.json` | false | Rendered unchanged | A 1.4 file whose rows carry no 1.4 value |
| `executorch-runtimes-in-1.3.json` | true | Rendered; the runtimes are harmless (not sent to the client); engine and binding versions shown | The ExecuTorch row in a file that declares 1.3 |
| `binding-only-in-1.3.json` | true | Rendered; the runtime is harmless (not sent to the client); binding version shown | `react-native-executorch` alone in a file that declares 1.3 |
| `unknown-runtime-name.json` | true | Shown | A `name` outside the closed list (`XNNPACK`) |
| `engine-version-form.json` | true | Shown sanitised (e.g. "ExecuTorch v1.4.1") | With `runtimes`, an `engineVersion` outside the fixed forms |

## Contract and display

Contracts 1.1 to 1.4 above cover the first rows of this table, and [Measurement sets](#measurement-sets-what-the-board-renders)
overrides it where they differ. Agree further field names and types in the versioned schema
before implementation.

| Data | Website requirement |
| --- | --- |
| Schema version and publication date | Validate on fetch; use a compatible fallback on failure and show its age. |
| Session, protocol, workload and evidence references | Link verified results to immutable, sanitized evidence; preserve historical links. |
| Engine/build versions, artifact hashes and actual backend/fallback | Engine and binding versions as text on every row that has them; configuration and hashes in result details (hashes never inline); unknown execution stays unknown. |
| Quantization, timing method, repetition count and dispersion | Make metric differences and uncertainty visible; missing values are not zero. |
| Condition policy, readiness, sampled conditions and verdict | In result details only, in plain words; the board holds no failed row; PSS/RSS samples are not peak memory. |
| Provenance, validation and comparison eligibility | Vendor claims in their own section; lab rows show versions or, without them, their month; compare only compatible records. |
| Later: quality and sustained measurements | Add workload selection with #5 and quality filtering with #6; keep metrics distinct. |

Lab provenance alone does not establish verification. Do not choose winners
from unsupported precision. Do not expose serials, local paths or raw logs in
the UI, downloads, analytics or error reports.

## Migration acceptance

- Land and deploy consumer support and compatible bundled fixtures before the
  producer change merges (PM-gated).
- Test legacy, verified, failed-condition, vendor, missing-quality and unsupported
  schema records, plus remote failures and stale fallback.
- Confirm incompatible records cannot produce a misleading comparison.
- Recompute a displayed median from linked evidence and check historical links.
- Make explanations usable on mobile and by keyboard, without hover or color alone.
- Update public claims only after the corresponding producer behavior is verified.

Deliver website changes through PRs in the website repository.
