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

There is no `eligibility` field in 1.1; it is deferred to 1.2 and rejected
until then. Unknown row fields are rejected (`additionalProperties: false`), so
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

## Contract and display

Contract 1.1 above covers the first rows of this table. Agree further field
names and types in the versioned schema before implementation.

| Data | Website requirement |
| --- | --- |
| Schema version and publication date | Validate on fetch; use a compatible fallback on failure and show its age. |
| Session, protocol, workload and evidence references | Link verified results to immutable, sanitized evidence; preserve historical links. |
| Engine/build versions, artifact hashes and actual backend/fallback | Expose configuration in result details; unknown execution stays unknown. |
| Quantization, timing method, repetition count and dispersion | Make metric differences and uncertainty visible; missing values are not zero. |
| Condition policy and endpoint snapshots | Distinguish failed/unknown from full verification; endpoint PSS is not peak memory. |
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
