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
`validation: "legacy"` and refuses to emit `verified` until an evidence
pipeline exists, so 1.1 data currently contains legacy and vendor rows only.

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
one optional field, on measured rows only, that names each software component
behind a row with its version. A file declares `schemaVersion` "1.2" only when a
row carries it. The same rollout rule applies: the website accepts 1.2 in
production before the producer publishes a 1.2 file.

| Field | Type | Meaning | Invariants |
| --- | --- | --- | --- |
| `build.runtimes` | `[{ name, version }]` | Each component that ran, with its version | Measured rows only, in `schemaVersion` "1.2" files only. Non-empty; one entry per `name`. `name` is one of `llama.cpp`, `llama.rn`, `LiteRT-LM`, `litert-npu-runtime-qualcomm`, `QAIRT`, `Genie`. `version` is a llama.cpp build (`b11385`) or a dotted version with an optional pre-release (`0.13.0-rc.7`, `2.50.0.260828221209`): `^(b[0-9]{1,7}\|[0-9]{1,5}(\.[0-9]{1,12}){1,3}(-[0-9A-Za-z]{1,16}(\.[0-9A-Za-z]{1,16}){0,3})?)$`. No other keys. |
| `build.engineVersion` | string | The engine's own version | With `runtimes`: `llama.cpp b<build> (<7-hex commit>)`, `LiteRT-LM <version>` or `Genie <version>`. Without `runtimes`, any string, as in 1.1. |
| `build.bindingVersion` | string | The app binding the engine runs through | With `runtimes`: `llama.rn <version>`. Without `runtimes`, any string, as in 1.1. |

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
session); a component the session does not report is absent, never guessed.

Consumer fixtures are in
[`tests/fixtures/contract-1.2/`](../tests/fixtures/contract-1.2/), listed in
`index.json`:

| Fixture | Expect | Covers |
| --- | --- | --- |
| `llama-cpp-runtimes.json` | accept | Synthetic llama.cpp row: engine build, llama.rn binding, both as runtimes |
| `litert-npu-runtimes.json` | accept | Synthetic LiteRT-LM NPU row: LiteRT-LM, Qualcomm NPU dispatch runtime and QAIRT |
| `v1.1-rows-in-1.2.json` | accept | A 1.2 file whose rows carry no 1.2 field |
| `runtimes-in-1.1.json` | reject | `build.runtimes` in a file that declares 1.1 |
| `duplicate-runtime.json` | reject | Two entries with the same `name` |
| `unknown-runtime-name.json` | reject | A `name` outside the closed list |
| `bad-runtime-version.json` | reject | A free-text `version` |
| `engine-version-form.json` | reject | With `runtimes`, an `engineVersion` outside the fixed forms |
| `runtimes-on-vendor-row.json` | reject | `build` on a vendor row |

## Contract and display

Contracts 1.1 and 1.2 above cover the first rows of this table. Agree further field
names and types in the versioned schema before implementation.

| Data | Website requirement |
| --- | --- |
| Schema version and publication date | Validate on fetch; use a compatible fallback on failure and show its age. |
| Session, protocol, workload and evidence references | Link verified results to immutable, sanitized evidence; preserve historical links. |
| Engine/build versions, artifact hashes and actual backend/fallback | Expose configuration in result details; unknown execution stays unknown. |
| Quantization, timing method, repetition count and dispersion | Make metric differences and uncertainty visible; missing values are not zero. |
| Condition policy, readiness, sampled conditions and verdict | Distinguish pass, failed and unverified conditions, and skipped repetitions; show charging-policy status separately; PSS/RSS samples are not peak memory. |
| Provenance, validation and comparison eligibility | Distinguish vendor claims, legacy and verified evidence; compare only compatible records. |
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
