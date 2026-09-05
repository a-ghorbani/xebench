# Reproducibility roadmap

Goal: anyone can retrieve a result's evidence, recompute its statistics, and
repeat its workload. These are planned capabilities, not current guarantees.

[Tracker #9](https://github.com/a-ghorbani/xebench/issues/9) tracks progress.
Linked issues contain detailed scope and acceptance criteria.

## 1. Auditable measurements

| Issue | Done when |
| --- | --- |
| [#1 Session evidence](https://github.com/a-ghorbani/xebench/issues/1) | Unique sessions preserve repetitions, configuration, artifact identity and failures; sanitized exports have checksums. |
| [#3 Reference runs](https://github.com/a-ghorbani/xebench/issues/3) | A clean checkout builds and runs a versioned CPU configuration without source edits. |
| [#2 Run conditions](https://github.com/a-ghorbani/xebench/issues/2) | Device evidence supports condition checks, cooldown and cleanup; unknown conditions cannot pass. |
| [#4 Publication](https://github.com/a-ghorbani/xebench/issues/4) | Invalid input leaves published data unchanged; verified rows link to evidence that reproduces their statistics. |

Start with #1 and #3, then #2 and #4. Agree the
[website migration](WEBSITE-HANDOFF.md) before changing the published contract.

## 2. Comparable workloads and quality

- [#5 Workloads](https://github.com/a-ghorbani/xebench/issues/5): define compatible
  comparisons and timing boundaries; use a pilot to set repetition and uncertainty
  policy. Depends on #1–#4.
- [#6 Quality](https://github.com/a-ghorbani/xebench/issues/6): evaluate deployed
  artifacts against a declared reference and tolerance; join quality and speed
  by artifact identity. Missing quality cannot qualify a comparison.
  Depends on #1, #3 and #5.

## 3. Independent reproduction

[#7 External reproduction](https://github.com/a-ghorbani/xebench/issues/7):
two independent operators reproduce selected comparisons against predefined
tolerances and publish evidence, including discrepancies. Depends on #1–#6.

## Shared rules

- Deliver every implementation through a reviewed PR; no direct commits to main.
- Export only approved fields. Exclude unique device identifiers, account details,
  local paths, credentials, raw logs and restricted assets; keep diagnostics local.
- Preserve historical evidence. Missing metadata stays unknown or legacy.
  Keep provenance, validation and comparison eligibility separate.
- Validate app changes with a fresh build. Record checks and device-validation
  limits in each PR; update methodology when behavior changes.
