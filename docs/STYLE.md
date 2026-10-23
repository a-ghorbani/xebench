# Style rules for public copy

Applies to everything a visitor reads: the board at pocketpal.dev/leaderboard/engines (rendered
from `data/benchmarks.json` and `data/findings.json`), README.md, ENGINES.md and METHODOLOGY.md.
Lab notes under `docs/experiments/` and the history in [CORRECTIONS.md](./CORRECTIONS.md) may
narrate how a result was reached; nothing else may.

## Board text (the lab-book gate)

The board is not a lab book. A visitor reads it cold, once, and must understand every word.

1. **Current results only.** The board shows each engine's latest measurement set and nothing
   else: no superseded, retired or failed rows, no "was" and no "will be". History lives in
   `data/sessions/index.json` and [CORRECTIONS.md](./CORRECTIONS.md).
2. **No history or process narration.** Never "we previously", "marked failed", "withdrawn",
   "superseded", "corrected", "pending", "not yet re-measured", "roadmapped", and no "yet" at all
   ("not on the board yet" narrates a plan). Say what is true now.
3. **No internal labels.** Never `legacy`, `harness`, `locked`, `gate`, `epoch`, `staged`,
   `retired`, `carryover`, policy ids, prompt ids or configuration names in prose. Name the thing
   a visitor knows: "the benchmark app", "an output check", "July 2026".
4. **No inline hashes.** No commit or SHA-256 strings in prose; link to the evidence instead.
   A version field keeps the upstream build id it names (e.g. "llama.cpp b11385 (bf9a0cc)").
5. **Every status or label is self-explanatory.** If a visitor would need our docs to understand
   a word, a badge or a column, rewrite it or drop it.
6. **Versions and dates, not states.** A row shows the engine and binding version it was measured
   with and the month it was measured. A row from before version recording shows its month and no
   version; it never says why.

`node scripts/check_public_text.mjs` enforces 2 to 4 on every string the site renders: each
finding's headline and deck, and each row's displayed fields (device, model, quant, source,
versions, configuration; `asStated` on vendor rows only, because a lab row's `asStated` is not
rendered). It fails on a listed term and its common forms (e.g. "replaces", "corrections"), on a
40- or 64-character hex string, and in prose (a headline, deck or vendor note) on any hex id of 7
or more characters; a short build id in a version field passes. It also fails on a row label
outside the board vocabulary: one spelling per quant scheme
(llama.cpp's GGUF names as they are, `Q8`, `int4`, `4-bit QAT`, `4-bit QAT + LoRA`, `W4A16`, `BF16`, ...; no
"not stated": leave an unknown quant out), upper-case SoC ids as in `configs/devices.json`,
device names as "Brand model" (Samsung Galaxy S23, Google Pixel 9, POCO F8 Ultra, Apple iPhone
17 Pro), and phone platforms only. A file that does not use one scheme throughout says so in
plain words. ExecuTorch's `8da4w` files store 4-bit weights and compute with 8-bit activations:
`4-bit weights, 8-bit activations`. A file whose weights mix bit widths names the widths it mixes:
`mixed 2/4/8-bit` (LiteRT-LM's Gemma 4 E2B file) and `mixed 4/16-bit` (ExecuTorch's Gemma 4 E2B
file, 4-bit layers with 16-bit attention). A mixed label states no single bit width, and the row's
notes say which parts use which width. Speeds are written "tok/s". A justified exception
goes in [`configs/public-text-allowlist.json`](../configs/public-text-allowlist.json) with a
reason; rewriting is almost always better. CI runs the check on every pull request.

## First-time visitor review

Every pull request that changes what the board shows (`data/benchmarks.json`,
`data/findings.json`, `configs/board.json`) or public copy completes this before it merges
(the checklist is in [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md)):

1. A reviewer who did not write the change opens the **rendered** page (the deployed site, or a
   preview of it with this branch's data) and reads it cold, top to bottom, on a phone width.
2. They list every word, label, badge, number or empty cell a newcomer would not understand or
   could misread, and every place where the page narrates history or process.
3. The author fixes each item, or answers it in the pull request with why a newcomer reads it
   correctly.
4. Nothing merges until the list is empty. The pull request records the reviewer, the page they
   read and the final (empty) list.

## All public copy

1. **Present tense, current state.** Say what is true now ("X runs with Y"), not how we got here.
2. **Corrections get a log entry.** When a published claim was wrong, rewrite the copy to the
   correct current facts and put the history (what was published, what is true, why, PRs,
   evidence) in [CORRECTIONS.md](./CORRECTIONS.md). Docs (README, ENGINES, METHODOLOGY) may keep a
   short "Corrected YYYY-MM-DD; see docs/CORRECTIONS.md" label where a reader needs it; the board
   never shows one (`context.correctedAt` in a finding is data, not rendered).
3. **Scope every claim.** Name the version, device and model a result holds for (e.g. "with
   llama.rn 0.13.0-rc.7 on the POCO F8 Ultra, for Llama 3.2 1B"). Do not generalise beyond what
   was tested.
4. **No unpublished numbers on the board.** Speeds that are not published board rows (spikes,
   experiment rows) do not appear in findings, README, ENGINES or METHODOLOGY; point to the
   experiment doc instead.
5. **Cite evidence.** Link the experiment doc (commit-pinned when it can move) or the evidence
   bundle for every measured claim. In docs, a bundle link may show its hash; on the board it may
   not (rule 4 above).
6. **Row notes are provenance.** An `asStated` note in `data/benchmarks.json` states, in present
   tense, only the facts a reader needs to interpret that row. A vendor row's note is rendered and
   follows the board rules; a lab row's note is lab-book provenance and is not rendered.
7. **Plain words.** Short sentences, concrete nouns, no hype; say "wrong output", not
   "suboptimal results".
