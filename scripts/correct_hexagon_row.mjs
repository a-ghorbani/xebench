#!/usr/bin/env node
/**
 * correct_hexagon_row.mjs — one documented data correction (2026-10-17).
 *
 *   node scripts/correct_hexagon_row.mjs           # apply in place (atomic), or no-op if already applied
 *   node scripts/correct_hexagon_row.mjs --check   # exit 1 if the correction is still needed; never writes
 *
 * The board's only llama.cpp NPU row (POCO F8 Ultra, Llama 3.2 1B, Q4_0, Hexagon)
 * was published as validation "legacy" without an output check. A 2026-10-17
 * re-check on the same phone with llama.rn 0.12.4, the version locked in
 * xebenchapp/package-lock.json when this row was measured (docs/experiments/
 * litert-accelerators.md, "Legacy Hexagon row check"), found llama.cpp's Hexagon
 * output wrong on prompts longer than about 128 tokens; this row used a ~512-token
 * prompt. This says nothing about other llama.rn versions.
 *
 * The correction sets that row's validation to "failed" and appends CORRECTION_NOTE
 * to its asStated. Its numbers stay as published (history is preserved); every
 * other row stays byte-identical; generatedAt is re-stamped only when it writes.
 *
 * Fail-closed: the row is matched on its identity AND its published values. If no
 * row, more than one row, or a row with different values is found, or the input
 * or result fails validation, it exits 1 and leaves the file byte-identical.
 * Exit codes: 0 ok (or nothing to do), 1 error or (--check) correction needed, 2 usage.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { assertBenchmarks, isoNow, PublicationError, readJson, writeAtomic } from './lib/publication.mjs';

export const ROW_IDENTITY = Object.freeze({
  engine: 'llama.cpp', backend: 'npu', platform: 'android', device: 'POCO F8 Ultra', soc: 'SM8850',
  model: 'Llama 3.2 1B', quant: 'Q4_0', provenance: 'measured',
});
export const PUBLISHED_VALUES = Object.freeze({ ppTps: 197.6, tgTps: 38.6, ttftMs: 2396, sampleCount: 3 });
export const ORIGINAL_AS_STATED = 'n=3 cold-run median; prompt std-pp512-v1; decode 128 tok';
export const CORRECTION_NOTE =
  'Marked failed 2026-10-17: a re-check on this phone with llama.rn 0.12.4 (the version this row was ' +
  'measured with) found llama.cpp Hexagon output wrong for prompts over ~128 tokens; this row used a ' +
  '~512-token prompt and had no output check, so its speed cannot be taken as correct inference.';
export const CORRECTED_AS_STATED = `${ORIGINAL_AS_STATED}; ${CORRECTION_NOTE}`;

const args = process.argv.slice(2);
const check = args.includes('--check');
if (args.some((a) => a !== '--check')) {
  console.error('usage: node scripts/correct_hexagon_row.mjs [--check]');
  process.exit(2);
}

const DATA = fileURLToPath(new URL('../data/benchmarks.json', import.meta.url));
const SCHEMA = fileURLToPath(new URL('../data/benchmarks.schema.json', import.meta.url));

const matches = (row, fields) => Object.entries(fields).every(([k, v]) => row[k] === v);

try {
  const schema = readJson(SCHEMA, 'data/benchmarks.schema.json');
  let before;
  try {
    before = readFileSync(DATA, 'utf8');
  } catch (error) {
    throw new PublicationError(`cannot read data/benchmarks.json: ${error.message}`);
  }
  const envelope = readJson(DATA, 'data/benchmarks.json');
  assertBenchmarks(envelope, schema, 'existing data/benchmarks.json');

  const indexes = envelope.rows.flatMap((row, i) => (matches(row, ROW_IDENTITY) ? [i] : []));
  if (indexes.length !== 1) throw new PublicationError(`expected exactly 1 target row, found ${indexes.length}`);
  const index = indexes[0];
  const row = envelope.rows[index];
  if (!matches(row, PUBLISHED_VALUES)) {
    throw new PublicationError(`target row values differ from the published ${JSON.stringify(PUBLISHED_VALUES)}`);
  }

  const applied = row.validation === 'failed' && row.asStated === CORRECTED_AS_STATED;
  const pending = row.validation === 'legacy' && row.asStated === ORIGINAL_AS_STATED;
  if (!applied && !pending) {
    throw new PublicationError(`target row is in an unexpected state (validation ${JSON.stringify(row.validation)})`);
  }
  if (check) {
    if (pending) {
      console.error('data/benchmarks.json needs the 2026-10-17 llama.cpp Hexagon row correction');
      process.exit(1);
    }
    console.error('data/benchmarks.json has the 2026-10-17 llama.cpp Hexagon row correction');
    process.exit(0);
  }
  if (applied) {
    console.error('data/benchmarks.json already has the llama.cpp Hexagon row correction; nothing to do');
    process.exit(0);
  }

  // Replace values in place: key order (and so every other line of the row) is unchanged.
  const corrected = { ...row, asStated: CORRECTED_AS_STATED, validation: 'failed' };
  const rows = envelope.rows.map((r, i) => (i === index ? corrected : r));
  const out = { ...envelope, generatedAt: isoNow(), rows };
  const text = JSON.stringify(out, null, 2) + '\n';
  assertBenchmarks(JSON.parse(text), schema, 'corrected data/benchmarks.json');
  writeAtomic(DATA, text, before);
  console.error(`corrected data/benchmarks.json row ${index}: validation legacy -> failed; 1 row changed, ` +
    `${rows.length - 1} unchanged`);
} catch (error) {
  console.error(error instanceof PublicationError ? `error: ${error.message}` : error);
  console.error('data/benchmarks.json was not modified.');
  process.exit(1);
}
