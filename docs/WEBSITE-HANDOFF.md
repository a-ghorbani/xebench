# Website handoff: auditable engine comparisons

Status: proposed consumer changes; no website changes or deployment performed.
Coordinate this contract with roadmap item D before publishing new-format data.

## Current integration

- `lib/engines/data.ts` fetches canonical xebench data with hourly revalidation
  and uses bundled data on failure. It currently selects measured/vendor rows.
- `lib/engines/types.ts` defines the consumer row shape.
- `components/leaderboard/EnginesExplorer.tsx` renders comparisons and source links.
- The engine leaderboard route consumes this data. Current source links for lab
  rows lead to methodology rather than an individual session's evidence.

## Required behavior

1. Validate the supported schema version and payload at the data boundary.
   An unsupported version or invalid response should use a compatible fallback
   and expose its date/staleness rather than implying current data.
2. Keep provenance separate from evidence validation: lab-measured does not
   automatically mean protocol-verified. Historical rows without new evidence
   remain identifiable as legacy/unverified, with a concise explanation.
3. Give each verified result an evidence link and detail view containing protocol,
   engine/build versions, artifact hashes, actual backend/fallback information,
   conditions, repetition count, dispersion, and measurement boundary.
4. Compare only compatible records using structured eligibility fields. Vendor
   claims remain useful reference material but must not acquire verified status
   merely by appearing next to lab results.
5. Display quantization and timing method near the metric. A callback-derived
   throughput number must not silently share an engine-counter comparison.
6. Explain missing measurements and insignificant differences. Do not substitute
   zero for missing values or assign a winner based on unsupported precision.
7. Add workload selection when E ships, then quality-qualified filtering when F
   ships. Keep TTFT, throughput, sustained behavior, memory and quality distinct.
8. Preserve usable links to historical results after the displayed session changes.

## Proposed contract fields

Finalize names and types in the shared schema before implementation:

- Session ID, protocol version, workload ID and immutable evidence URL/checksum.
- Harness/build and resolved engine versions; model/tokenizer identities.
- Requested backend and observed execution/fallback status.
- Per-metric measurement method, sample count and dispersion.
- Validation status, eligibility and structured exclusion reasons.
- Later: quality evaluation ID/status and sustained measurement references.

Do not infer verification from missing fields. Do not expose local paths, device
serials or raw diagnostic logs in tooltips, downloads, analytics or error reports.

## Migration and acceptance

- Land consumer support and compatible bundled fixtures before producer rollout.
- Test legacy, verified, failed-condition, vendor, missing-quality and unsupported
  schema fixtures; test remote failure and stale fallback behavior.
- Confirm incompatible records cannot generate a misleading winner.
- Verify keyboard/mobile access to explanations and evidence links; do not rely
  solely on hover tooltips or color.
- Verify that a displayed median can be independently recomputed from linked
  sanitized evidence, and that historical evidence links remain valid.
- Update public explanatory copy only when the corresponding producer guarantee
  is implemented and verified.
