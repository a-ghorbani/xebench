# Website handoff

Proposed PocketPal leaderboard changes for
[publication issue #4](https://github.com/a-ghorbani/xebench/issues/4).
This is a handoff, not an implementation or deployment report.

Integration points: `lib/engines/data.ts` (fetch/fallback),
`lib/engines/types.ts` (row types), and
`components/leaderboard/EnginesExplorer.tsx` (comparisons/evidence).

## Contract and display

Agree field names and types in the versioned schema before implementation.

| Data | Website requirement |
| --- | --- |
| Schema version and publication date | Validate on fetch; use a compatible fallback on failure and show its age. |
| Session, protocol, workload and evidence references | Link verified results to immutable, sanitized evidence; preserve historical links. |
| Engine/build versions, artifact hashes and actual backend/fallback | Expose configuration in result details; unknown execution stays unknown. |
| Quantization, timing method, repetition count and dispersion | Make metric differences and uncertainty visible; missing values are not zero. |
| Provenance, validation and comparison eligibility | Distinguish vendor claims, legacy and verified evidence; compare only compatible records. |
| Later: quality and sustained measurements | Add workload selection with #5 and quality filtering with #6; keep metrics distinct. |

Lab provenance alone does not establish verification. Do not choose winners
from unsupported precision. Do not expose serials, local paths or raw logs in
the UI, downloads, analytics or error reports.

## Migration acceptance

- Land consumer support and compatible bundled fixtures before producer rollout.
- Test legacy, verified, failed-condition, vendor, missing-quality and unsupported
  schema records, plus remote failures and stale fallback.
- Confirm incompatible records cannot produce a misleading comparison.
- Recompute a displayed median from linked evidence and check historical links.
- Make explanations usable on mobile and by keyboard, without hover or color alone.
- Update public claims only after the corresponding producer behavior is verified.

Deliver website changes through PRs in the website repository.
