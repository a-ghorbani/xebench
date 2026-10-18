# Style rules for public copy

Applies to everything a visitor reads: `data/findings.json` (rendered verbatim on
pocketpal.dev), README.md, ENGINES.md and METHODOLOGY.md. Lab notes under `docs/experiments/` may
narrate how a result was reached.

1. **Present tense, current state.** Say what is true now ("X runs with Y"), not how we got here.
2. **No history in findings or docs.** Never narrate our past mistakes ("we previously said",
   "that was wrong", "withdrawn") in the body.
3. **Corrections get a dated label and a log entry.** Leave one short label where a reader needs
   it: "Corrected YYYY-MM-DD; see docs/CORRECTIONS.md" in docs, `context.correctedAt` in a finding.
   The full story (what was published, what is true, why, PRs, evidence) goes in
   [CORRECTIONS.md](./CORRECTIONS.md).
4. **Scope every claim.** Name the version, device and model a result holds for (e.g. "with
   llama.rn 0.13.0-rc.7 on the POCO F8 Ultra, for Llama 3.2 1B"). Do not generalise beyond what
   was tested.
5. **No unpublished numbers on the board.** Speeds that are not published board rows (spikes,
   experiment rows) do not appear in findings, README or ENGINES; point to the experiment doc
   instead.
6. **Cite evidence.** Link the experiment doc (commit-pinned when it can move) or an evidence
   bundle hash for every measured claim.
7. **Plain words.** Short sentences, concrete nouns, no hype; say "wrong output", not
   "suboptimal results".
