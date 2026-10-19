#!/usr/bin/env node
/**
 * date_legacy_rows.mjs — give every legacy (July 2026) row its month (2026-10-19).
 *
 *   node scripts/date_legacy_rows.mjs           # apply in place (atomic), or no-op if already applied
 *   node scripts/date_legacy_rows.mjs --check   # exit 1 if a legacy row still needs its date; never writes
 *
 * Rows measured before the app recorded engine versions carry no version, so the board shows
 * their month instead (docs/RUN-CONDITIONS.md, Measurement sets). Most of them had no
 * sourceDate. Each row below is matched on its key AND its published numbers, and gets
 * sourceDate plus dateBasis (contract 1.3):
 *
 *   measured         the month comes from the run's own record: the raw record pulled off the
 *                    phone (rd-team evaluation/prototypes/llm-cross-engine-bench/results/,
 *                    files written 2026-07-17 to 2026-07-21, matching the row's numbers), the
 *                    dated run reports there (MORNING-REPORT.md, 2026-07-18, addendum
 *                    2026-07-21), or a record timestamp.
 *   first-published  no record with the row's numbers survives; the month is that of the
 *                    first commit carrying the row (f309080, 2026-07-22).
 *
 * Nothing is guessed: a legacy row that is not in the table, or whose numbers differ, is
 * refused. Every other field and every other row keeps its bytes; generatedAt is re-stamped
 * and schemaVersion becomes 1.3 only when the script writes.
 *
 * Fail-closed: the input and the result are validated; on any error it exits 1 and leaves
 * the file byte-identical. Exit codes: 0 ok (or nothing to do), 1 error or (--check) needed, 2 usage.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  assertBenchmarks, contractVersionFor, isoNow, PublicationError, readJson, rowKey, writeAtomic,
} from './lib/publication.mjs';

const M = 'measured';
const P = 'first-published';
/** key -> [ppTps, tgTps, sourceDate, dateBasis, basis]; the basis is documentation only. */
export const LEGACY_DATES = Object.freeze({
  'executorch|cpu|android|Google Pixel 9|Llama 3.2 1B|SpinQuant 4-bit|measured': [171.7, 15.9, '2026-07', M, 'raw record, Pixel 9, 2026-07-17/21'],
  'llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_K_M|measured': [144, 24.1, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_0|measured': [211.2, 25.9, '2026-07', M, 'raw record, Pixel 9, 2026-07-21'],
  'litert-lm|cpu|android|Google Pixel 9|Qwen2.5 1.5B|q8|measured': [77.3, 8.2, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'llama.cpp|cpu|android|Google Pixel 9|Qwen2.5 1.5B|Q8_0|measured': [135.7, 11.7, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'executorch|cpu|android|OnePlus 6|Llama 3.2 1B|SpinQuant 4-bit|measured': [11.4, 4.8, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'llama.cpp|cpu|android|OnePlus 6|Llama 3.2 1B|Q4_K_M|measured': [16.1, 8.6, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'llama.cpp|cpu|android|OnePlus 6|Qwen2.5 1.5B|Q8_0|measured': [4.9, 3.2, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'litert-lm|cpu|android|POCO F8 Ultra|Gemma 3 1B|int4|measured': [691.7, 40.6, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Gemma 3 1B|Q4_0 (QAT)|measured': [382.2, 42.7, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'executorch|cpu|android|POCO F8 Ultra|Llama 3.2 1B|SpinQuant 4-bit|measured': [311.9, 34.3, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'genie-x|npu|android|POCO F8 Ultra|Llama 3.2 1B|W4A16|measured': [354.6, 16.8, '2026-07', M, 'run report addendum and ENGINES.md, measured 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Llama 3.2 1B|Q4_0|measured': [300.3, 47.3, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Llama 3.2 1B|Q4_K_M|measured': [206.9, 43.4, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'litert-lm|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|q8|measured': [323.8, 24.2, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured': [236.3, 27.9, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|gpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured': [236.9, 31.6, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'litert-lm|cpu|android|Samsung Galaxy S23|Gemma 3 1B|int4|measured': [186.6, 36.1, '2026-07', P, 'no record with these numbers; first published in f309080, 2026-07-22'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Gemma 3 1B|Q4_0 (QAT)|measured': [236.1, 21.9, '2026-07', P, 'no record with these numbers; first published in f309080, 2026-07-22'],
  'executorch|cpu|android|Samsung Galaxy S23|Llama 3.2 1B|SpinQuant 4-bit|measured': [149.9, 21.1, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18/21'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Llama 3.2 1B|Q4_K_M|measured': [124, 25.9, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'genie-x|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [87.9, 12.3, '2026-07', M, 'raw record timestampIso 2026-07-18T02:35:00Z'],
  'litert-lm|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|q8|measured': [78.6, 16, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [105.2, 19, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'llama.cpp|gpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [97.6, 18.4, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
});

const isLegacy = (row) => row.provenance === 'measured' && row.validation === 'legacy';
const dated = (row) => {
  const [, , sourceDate, dateBasis] = LEGACY_DATES[rowKey(row)];
  return row.sourceDate === sourceDate && row.dateBasis === dateBasis;
};

/** The row with sourceDate and dateBasis placed after sourceUrl; every other key keeps its place. */
function withDate(row, where) {
  const entry = LEGACY_DATES[rowKey(row)];
  if (!entry) throw new PublicationError(`${where}: legacy row ${rowKey(row)} is not in LEGACY_DATES; its month is not established`);
  const [ppTps, tgTps, sourceDate, dateBasis] = entry;
  if (row.ppTps !== ppTps || row.tgTps !== tgTps) {
    throw new PublicationError(`${where}: ${rowKey(row)} has ${row.ppTps}/${row.tgTps}, not the dated ${ppTps}/${tgTps}`);
  }
  if (row.sourceDate !== undefined && row.sourceDate !== sourceDate) {
    throw new PublicationError(`${where}: ${rowKey(row)} has sourceDate ${row.sourceDate}, not ${sourceDate}`);
  }
  if (row.dateBasis !== undefined && row.dateBasis !== dateBasis) {
    throw new PublicationError(`${where}: ${rowKey(row)} has dateBasis ${row.dateBasis}, not ${dateBasis}`);
  }
  const out = {};
  for (const [field, value] of Object.entries(row)) {
    if (field === 'sourceDate' || field === 'dateBasis') continue;
    out[field] = value;
    if (field === 'sourceUrl') Object.assign(out, { sourceDate, dateBasis });
  }
  return out;
}

const args = process.argv.slice(2);
const check = args.includes('--check');
if (args.some((a) => a !== '--check')) {
  console.error('usage: node scripts/date_legacy_rows.mjs [--check]');
  process.exit(2);
}
const DATA = fileURLToPath(new URL('../data/benchmarks.json', import.meta.url));
const SCHEMA = fileURLToPath(new URL('../data/benchmarks.schema.json', import.meta.url));

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
  const rows = envelope.rows.map((row, index) => (isLegacy(row) ? withDate(row, `rows[${index}]`) : row));
  const pending = envelope.rows.filter((row) => isLegacy(row) && !dated(row));
  if (check) {
    if (pending.length) {
      console.error(`data/benchmarks.json: ${pending.length} legacy row(s) need their month (node scripts/date_legacy_rows.mjs)`);
      process.exit(1);
    }
    console.error('data/benchmarks.json: every legacy row has its month');
    process.exit(0);
  }
  if (!pending.length) {
    console.error('data/benchmarks.json: every legacy row already has its month; nothing to do');
    process.exit(0);
  }
  const out = { ...envelope, schemaVersion: contractVersionFor(rows, envelope.schemaVersion), generatedAt: isoNow(), rows };
  const text = JSON.stringify(out, null, 2) + '\n';
  assertBenchmarks(JSON.parse(text), schema, 'dated data/benchmarks.json');
  writeAtomic(DATA, text, before);
  console.error(`dated ${pending.length} legacy row(s); schemaVersion ${out.schemaVersion}`);
} catch (error) {
  console.error(error instanceof PublicationError ? `error: ${error.message}` : error);
  console.error('data/benchmarks.json was not modified.');
  process.exit(1);
}
