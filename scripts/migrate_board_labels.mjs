#!/usr/bin/env node
/**
 * migrate_board_labels.mjs — board label migration of 2026-10-19 (website lab-book review).
 *
 *   node scripts/migrate_board_labels.mjs           # apply in place (atomic), or no-op if applied
 *   node scripts/migrate_board_labels.mjs --check   # exit 1 if the migration is still needed; never writes
 *
 * Display labels, no measurement change:
 *   sourceName  lab rows say "xebench" (was "PocketPal R&D lab (xebench)").
 *   chipName    (contract 1.2+, optional) the SoC's marketing name: on lab rows from
 *               configs/devices.json (by device name), on vendor rows from VENDOR_CHIPS below,
 *               only where the device ships with one chip. Placed after soc (or device).
 *   quantBits   (contract 1.3, optional) the weight bit width the quant names, where certain
 *               (publication.mjs quantBitsFor). Placed after quant.
 *   vendor      quant schemes in one order (VENDOR_QUANTS), one source title and one note
 *               (VENDOR_TEXT); a verified row's quant is never changed.
 *
 * The rows in data/benchmarks.json and the session rows in data/sessions/index.json change
 * together, because each displayed row equals its session's row. scripts/lib/bundles.mjs
 * produces the same two labels, so every session still reproduces from its bundle. This is a
 * label migration of the published index (docs/RUN-CONDITIONS.md, Publication policy v1):
 * replacedRows keep the rows as they were. Sources for each chip name: docs/WEBSITE-HANDOFF.md,
 * Chip names.
 *
 * Fail-closed: both files are validated before and after; written together or not at all.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  assertBenchmarks, compareRows, contractVersionFor, isoNow, PublicationError, quantBitsFor, readJson, writeAtomicGroup,
} from './lib/publication.mjs';
import { assertIndex } from './lib/history.mjs';

export const SOURCE_NAME = 'xebench';
const OLD_SOURCE_NAME = 'PocketPal R&D lab (xebench)';
/** Vendor devices that ship with exactly one chip. Not listed: Galaxy S22, S24 (chip by region), S26 Ultra (not verified). */
export const VENDOR_CHIPS = Object.freeze({
  'Google Pixel 7 Pro': 'Google Tensor G2',
  'Google Pixel 8 Pro': 'Google Tensor G3',
  'OnePlus 12': 'Snapdragon 8 Gen 3',
  'OnePlus 12 (16GB)': 'Snapdragon 8 Gen 3',
  'Samsung Galaxy S24 Ultra': 'Snapdragon 8 Gen 3 for Galaxy',
  'Apple iPhone 17 Pro': 'Apple A19 Pro',
});

/** Vendor quant schemes in one readable order (same scheme, same meaning). */
export const VENDOR_QUANTS = Object.freeze({ 'QAT+LoRA 4-bit': '4-bit QAT + LoRA', 'int4 QAT': '4-bit QAT' });
/** Vendor source titles and notes in plain words: [field, before, after]. */
export const VENDOR_TEXT = Object.freeze([
  ['sourceName', 'Arm Learning Path (ExecuTorch + XNNPACK)', 'Arm Learning Path (ExecuTorch tutorial)'],
  ['asStated', 'Decode on the GPU; the source reports up to 2.2x more with multi-token prediction.', 'Measured without an optional speed-up mode (multi-token prediction); the source reports up to 2.2x faster generation with it.'],
  ['asStated', 'Measured without multi-token prediction; the source reports up to 2.2x faster generation with it.', 'Measured without an optional speed-up mode (multi-token prediction); the source reports up to 2.2x faster generation with it.'],
]);

const args = process.argv.slice(2);
const check = args.includes('--check');
if (args.some((a) => a !== '--check')) {
  console.error('usage: node scripts/migrate_board_labels.mjs [--check]');
  process.exit(2);
}
const url = (path) => fileURLToPath(new URL(`../${path}`, import.meta.url));
const DATA = url('data/benchmarks.json');
const INDEX = url('data/sessions/index.json');

/** The row with its labels; every other key keeps its place. */
function labelled(row, labChips) {
  const vendor = row.provenance !== 'measured';
  const chipName = vendor ? VENDOR_CHIPS[row.device] : labChips.get(row.device);
  const quant = vendor && VENDOR_QUANTS[row.quant] ? VENDOR_QUANTS[row.quant] : row.quant;
  const quantBits = quantBitsFor(quant);
  const anchor = Object.hasOwn(row, 'soc') ? 'soc' : 'device';
  const out = {};
  for (const [field, value] of Object.entries(row)) {
    if (field === 'chipName' || field === 'quantBits') continue;
    let next = field === 'sourceName' && !vendor && value === OLD_SOURCE_NAME ? SOURCE_NAME : field === 'quant' ? quant : value;
    if (vendor) for (const [name, before, after] of VENDOR_TEXT) if (field === name && value === before) next = after;
    out[field] = next;
    if (field === anchor && chipName) out.chipName = chipName;
    if (field === 'quant' && quantBits !== null) out.quantBits = quantBits;
  }
  return out;
}

try {
  const schema = readJson(url('data/benchmarks.schema.json'), 'data/benchmarks.schema.json');
  const schemas = { benchmarks: schema, row: schema.$defs.row, index: readJson(url('data/sessions/index.schema.json'), 'data/sessions/index.schema.json') };
  const before = readFileSync(DATA, 'utf8');
  const indexBefore = readFileSync(INDEX, 'utf8');
  const envelope = JSON.parse(before);
  const index = JSON.parse(indexBefore);
  assertBenchmarks(envelope, schema, 'existing data/benchmarks.json');
  const table = readJson(url('configs/devices.json'), 'configs/devices.json');
  const labChips = new Map(table.devices.filter((entry) => entry.chipName).map((entry) => [entry.name, entry.chipName]));
  const rows = envelope.rows.map((row) => labelled(row, labChips)).sort(compareRows);
  const keys = index.keys.map((entry) => ({
    ...entry, sessions: entry.sessions.map((session) => ({ ...session, row: labelled(session.row, labChips) })),
  }));
  const changed = JSON.stringify(rows) !== JSON.stringify(envelope.rows) || JSON.stringify(keys) !== JSON.stringify(index.keys);
  if (check) {
    console.error(changed ? 'the board label migration is needed (node scripts/migrate_board_labels.mjs)' : 'board labels are migrated');
    process.exit(changed ? 1 : 0);
  }
  if (!changed) {
    console.error('board labels are already migrated; nothing to do');
    process.exit(0);
  }
  const out = { ...envelope, schemaVersion: contractVersionFor(rows, envelope.schemaVersion), generatedAt: isoNow(), rows };
  const text = JSON.stringify(out, null, 2) + '\n';
  const indexText = JSON.stringify({ ...index, keys }, null, 2) + '\n';
  assertBenchmarks(JSON.parse(text), schema, 'migrated data/benchmarks.json');
  assertIndex(JSON.parse(indexText), JSON.parse(text), schemas, 'migrated data/sessions/index.json');
  writeAtomicGroup([{ target: INDEX, text: indexText, before: indexBefore }, { target: DATA, text, before }]);
  console.error(`migrated board labels: ${rows.filter((row, i) => JSON.stringify(row) !== JSON.stringify(envelope.rows[i])).length} row(s)`);
} catch (error) {
  console.error(error instanceof PublicationError ? `error: ${error.message}` : error);
  console.error('data/benchmarks.json and data/sessions/index.json were not modified.');
  process.exit(1);
}
