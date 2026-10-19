#!/usr/bin/env node
/**
 * date_legacy_rows.mjs — give every legacy (July 2026) row its month (2026-10-19).
 *
 *   node scripts/date_legacy_rows.mjs           # apply in place (atomic), or no-op if already applied
 *   node scripts/date_legacy_rows.mjs --check   # exit 1 if a legacy row still needs its date; never writes
 *
 * Rows measured before the app recorded engine versions carry no version, so the board shows
 * their month instead (docs/RUN-CONDITIONS.md, Measurement sets). Most of them had no
 * sourceDate. Each row in scripts/lib/legacy-dates.mjs (LEGACY_DATES, with what each month
 * rests on) is matched on its key AND its published numbers, and gets sourceDate plus dateBasis
 * (contract 1.3). The same run puts its labels into the board vocabulary (LEGACY_LABELS:
 * LiteRT-LM "q8" -> "Q8", SoC "sdm845" -> "SDM845"); the meaning is unchanged.
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
import { LEGACY_DATES, withBoardLabels } from './lib/legacy-dates.mjs';
import { isLegacy } from './lib/history.mjs';

const dated = (row) => {
  const entry = LEGACY_DATES[rowKey(row)];
  return Boolean(entry) && row.sourceDate === entry[2] && row.dateBasis === entry[3] &&
    JSON.stringify(withBoardLabels(row)) === JSON.stringify(row);
};

/** The row in the board vocabulary with sourceDate and dateBasis after sourceUrl; every other key keeps its place. */
function withDate(original, where) {
  const row = withBoardLabels(original);
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
