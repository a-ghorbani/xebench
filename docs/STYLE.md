# Style rules for public copy

Applies to everything a visitor reads: `data/findings.json` (headline, deck and details link
rendered verbatim on pocketpal.dev), README.md, ENGINES.md and METHODOLOGY.md. Lab notes under `docs/experiments/` may
narrate how a result was reached.

1. **The board shows the final state.** Findings are a short title, at most two sentences and a
   details link; the process, evidence and history live in docs/.
2. **Present tense, current state.** Say what is true now ("X runs with Y"), not how we got here.
3. **No history in public copy.** Never narrate our past mistakes ("we previously said",
   "that was wrong", "withdrawn") in the body.
4. **Corrections get a dated label and a log entry.** Leave one short label where a reader needs
   it: "Corrected YYYY-MM-DD; see docs/CORRECTIONS.md" in docs, `context.correctedAt` in a finding.
   The full story (what was published, what is true, why, PRs, evidence) goes in
   [CORRECTIONS.md](./CORRECTIONS.md).
5. **Scope every claim.** Name the version, device and model a result holds for (e.g. "with
   llama.rn 0.13.0-rc.7 on the POCO F8 Ultra, for Llama 3.2 1B"). Do not generalise beyond what
   was tested.
6. **No unpublished numbers on the board.** Speeds that are not published board rows (spikes,
   experiment rows) do not appear in findings, README, ENGINES or METHODOLOGY; point to the
   experiment doc instead.
7. **Cite evidence.** Link the experiment doc (commit-pinned when it can move) or an evidence
   bundle hash for every measured claim.
8. **Row notes are provenance.** An `asStated` note in `data/benchmarks.json` states, in present
   tense, only the facts a reader needs to interpret that row.
9. **Plain words.** Short sentences, concrete nouns, no hype; say "wrong output", not
   "suboptimal results".
