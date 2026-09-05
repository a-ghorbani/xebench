# Roadmap: independently reproducible xebench

Status: proposed implementation plan, 2026-09-05. This document describes
acceptance criteria, not guarantees already provided by the harness.

The objective is to let a third party reproduce a result, inspect its evidence,
and understand which comparisons it supports. PocketPal is the first consumer;
the protocol and data should also be usable independently of the website.

## Tracking and publication

Use one GitHub tracking issue for this roadmap and one issue per work item below.
Copy each item's problem, scope, and acceptance criteria into its issue. Keep
implementation discussion in the corresponding pull request. A GitHub Project
is optional; issue dependencies and a milestone are sufficient initially.

Suggested milestones:

1. Auditable measurements: A–D, with the website handoff agreed before changing
   the public data contract.
2. Comparable workloads and quality: E–F.
3. Independent reproduction: G.

Public issues should discuss observable behavior and desired capabilities.
Do not include device serials, account identifiers, workstation paths, private
logs, credentials, proprietary libraries, or model weights. Evidence exports
must use an explicit allowlist and synthetic fixtures for privacy tests.
Device make, model, SoC, OS and relevant runtime versions are useful public
benchmark metadata; unique device identifiers are not.

Existing measurements retain their historical provenance. Missing evidence
must be represented as unknown or legacy, never reconstructed from assumptions.
New validation must not silently delete or relabel historical measurements.

## A. Preserve complete, immutable benchmark session evidence

Problem: session output currently retains summary statistics without the full
set of repetition measurements. Capture filenames can collide across repeated
sessions and quantizations.

Scope:

- Define and validate a versioned raw session schema.
- Record a unique session ID, UTC timestamp, harness/build identity, resolved
  engine/binding versions, model/tokenizer hashes and effective settings.
- Preserve every repetition's metrics, actual token counts, measurement methods,
  load duration and completion/failure status. Use monotonic elapsed timing.
- Distinguish requested backend from confirmed execution/fallback information;
  unknown execution must remain unknown.
- Capture to unique files without serials in filenames. Never overwrite evidence.
- Export a sanitized manifest with checksums; keep original diagnostic logs local.

Acceptance:

- Repeated sessions and two quantizations cannot overwrite one another.
- A reader can recompute medians and dispersion from individual measurements.
- Invalid, truncated, non-finite and incomplete records fail validation clearly.
- Export tests demonstrate that private identifiers and arbitrary log fields do
  not enter public artifacts.

Dependencies: none. Device validation follows C's reproducible build path.

## B. Measure and enforce benchmark run conditions

Problem: the current runner marks guards as passed without measuring the
conditions described in the methodology.

Scope:

- Add native probes for battery, charging, power saving, thermal state and memory;
  record unsupported probes explicitly.
- Check foreground/screen conditions and record conditions before and after runs.
- Implement protocol-defined cooldown and thermal readiness with bounded waiting.
- Release resources on failure, and record failed/skipped runs explicitly.
- Distinguish fresh model instances, warm requests and process restarts.
- Balance or randomize engine order with a recorded seed; define charging policy.
- Reconcile methodology text with implemented, tested behavior, including the
  present CPU/GPU/NPU scope. Avoid generalizing device-specific observations.

Acceptance:

- Passing guards require measured evidence; unknown is not a pass.
- Unit tests cover failed and unavailable probes, cooldown timeout and cleanup.
- Device runs demonstrate condition capture and interruption handling.
- Every claimed enforcement rule links to its implementation and validation.

Dependencies: A for persisted evidence; C for repeatable device execution.

## C. Make reference runs configurable and builds reproducible

Problem: the checked-in run selection is a focused experiment, and incompatible
native runtimes require manual source/build edits.

Scope:

- Replace source-edited run lists with validated, versioned run configurations.
- Add explicit build flavors for incompatible native runtime combinations.
- Pin and record resolved dependencies, native SDK requirements and build inputs.
- Provide a documented CPU reference path before optional accelerator paths.
- Document lawful model/SDK acquisition and checksum verification; do not bundle
  assets without redistribution rights.
- Make host automation report timeout, partial success and native failures with
  useful exit codes. Keep device identifiers out of exported artifacts.

Acceptance:

- A clean checkout can build the reference flavor using documented prerequisites.
- A reference run needs no edits to App.tsx or packaging exclusions.
- The manifest identifies the configuration and binary used for each session.
- Smoke validation on at least two available device families is recorded locally;
  publish only sanitized, protocol-compliant evidence.

Dependencies: none; integrate A and B before claiming protocol compliance.

## D. Validate publication and expose per-result evidence

Problem: aggregation loses runtime identity and does not link a row to its
specific evidence. The declared schema is not enforced by the publication path.

Scope:

- Validate raw input and output schemas before any canonical-data write.
- Derive statistics from validated repetitions and verify reported sample counts.
- Publish protocol version, session/evidence references, timing methods,
  uncertainty and eligibility in structured fields.
- Preserve historical sessions; define explicit selection of the displayed
  result instead of implicit last-file-wins behavior.
- Keep provenance (who supplied it) separate from validation status and comparison
  eligibility. Failed, unknown and legacy records must not imply verified status.
- Make updates atomic and fail closed on malformed existing canonical data.
- Add CI checks for schemas, aggregation, evidence integrity and privacy fixtures.
- Coordinate consumer migration before publishing a breaking schema version.

Acceptance:

- An invalid input leaves canonical data unchanged and returns failure.
- A verified row resolves to immutable evidence and reproducible statistics.
- Vendor and legacy rows survive migration without invented metadata.
- Deterministic fixture tests cover collisions, ordering, failed guards and
  incompatible protocol versions.

Dependencies: A and B; website contract in WEBSITE-HANDOFF.md.

## E. Define comparable performance workloads

Problem: a single prompt and decode length cannot describe all mobile use, and
engine counters and application callbacks measure different boundaries.

Scope:

- Version short-chat, long-input and sustained-conversation workloads with exact
  prompts, model-appropriate formatting, output limits and cache policies.
- Define application TTFT separately from engine prefill/decode counters; record
  callback batching and actual tokens rather than assuming one callback per token.
- Define comparison keys covering device, workload, protocol, timing boundary,
  model family and execution conditions. Label intentionally different settings.
- Use pilot variance to choose repetitions and independent sessions; publish
  dispersion and declare practically insignificant differences, not just winners.
- Publish sustained performance and memory separately. Treat energy as exploratory
  until a validated measurement method is available.

Acceptance:

- Comparison eligibility is machine-checkable and covered by fixtures.
- Tokenization mismatches, early EOS and fallback cannot silently appear equivalent.
- A documented pilot supports the repetition and uncertainty policy.

Dependencies: A–D.

## F. Add quality evaluation of deployed model artifacts

Problem: engine-native quantizations may trade quality for speed, so throughput
alone cannot establish equivalence.

Scope:

- Select a small, redistributable, versioned task suite and model-appropriate
  higher-precision reference. Document evaluation and contamination limitations.
- Run the actual deployment artifacts through each engine, preserving settings,
  tokenizer identity, outputs and scoring version.
- Define quality tolerances before ranking configurations and report uncertainty.
- Use perplexity/logits-based diagnostics only where comparable APIs exist;
  missing support must not be disguised as a quality pass.
- Present performance/quality tradeoffs without an arbitrary composite score.

Acceptance:

- Quality results join performance records by exact artifact identity.
- Evaluation is repeatable, with a documented reference and scoring procedure.
- Missing or failed quality evidence is visible and excluded from quality-qualified
  comparisons.

Dependencies: A, C and E.

## G. Reproduce the reference matrix independently

Problem: reproducibility must be demonstrated by operators outside the originating
lab, not inferred solely from open source availability.

Scope:

- Freeze a small reference matrix and release protocol/configuration bundle.
- Provide installation, model acquisition, run and sanitized submission steps.
- Invite at least two independent operators to reproduce selected comparisons.
- Predeclare tolerances and publish discrepancies with evidence and explanations.
- Document submissions, review, conflicts of interest, corrections and appeals;
  invite engine maintainers to review configuration choices.

Acceptance:

- Two external reproduction reports link to complete evidence bundles.
- Another operator can reproduce the process without author-only setup knowledge.
- Results are versioned and corrections preserve the historical record.

Dependencies: A–F. External outreach is a separate action from repository work.

## Immediate execution order

1. Inventory build prerequisites and device readiness locally. Use only the
   agreed device availability window, and stop workloads when that window ends
   unless availability is extended. Do not reset devices or remove other apps.
2. Implement A's schema, immutable capture and focused fixture tests, alongside
   C's minimal reproducible CPU build/configuration path.
3. Implement B and collect a small device pilot. An installed older binary can
   establish readiness, but its outputs cannot validate new source changes.
4. Implement D and reconcile documentation. Keep new-format data unpublished
   until the consumer supports the agreed contract.
5. Deliver a reviewable change with test results and explicit device-validation
   limitations. Continue E–G as separate work items.

The device window is an opportunity for a pilot, not a promise that all build
prerequisites or the full reference matrix can be completed within an hour.
