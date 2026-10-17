#!/usr/bin/env node
/**
 * aggregate.mjs — convert raw xebench JSONL (one EngineSessionRecord per line,
 * pulled off-device) into EngineBenchmarkRow[] matching
 * pocketpal-website/lib/engines/types.ts and data/benchmarks.schema.json.
 *
 * Usage:
 *   node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]          # preview rows on stdout
 *   node scripts/aggregate.mjs <raw.jsonl> [...] --update --allow-legacy
 *                                                                     # upsert into data/benchmarks.json
 *
 * Fail-closed: any malformed, truncated, non-finite, unsupported or inconsistent
 * input line aborts the whole run with exit code 1 and no output/write. With
 * --update, the existing canonical file and the result are schema-validated and
 * the file is replaced atomically, or left byte-identical on any failure.
 *
 * It does NOT invent numbers: every field comes from the on-device record.
 */
import { closeSync, fsyncSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import {
  assertBenchmarks, assertFinite, assertSchema, compareRows, PublicationError, readJson, rowKey,
} from './lib/publication.mjs';

const USAGE =
  'usage:\n' +
  '  node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]                  # preview EngineBenchmarkRow[] on stdout\n' +
  '  node scripts/aggregate.mjs <raw.jsonl> [...] --update --allow-legacy    # upsert reviewed legacy rows into data/benchmarks.json\n' +
  'exit codes: 0 ok, 1 rejected input/canonical data (nothing written), 2 usage';

const FLAGS = new Set(['--pretty', '--update', '--allow-legacy']);
const args = process.argv.slice(2);
const unknown = args.filter((a) => a.startsWith('--') && !FLAGS.has(a));
const pretty = args.includes('--pretty');
const update = args.includes('--update'); // upsert into the canonical data/benchmarks.json
const allowLegacy = args.includes('--allow-legacy');
const files = args.filter((a) => !a.startsWith('--'));
if (files.length === 0 || unknown.length || (allowLegacy && !update)) {
  if (unknown.length) console.error(`unknown option(s): ${unknown.join(' ')}`);
  if (allowLegacy && !update) console.error('--allow-legacy only applies to --update');
  console.error(USAGE);
  process.exit(2);
}

const DATA = fileURLToPath(new URL('../data/benchmarks.json', import.meta.url));
const SCHEMA = fileURLToPath(new URL('../data/benchmarks.schema.json', import.meta.url));
const SOURCE_NAME = 'PocketPal R&D lab (xebench)';
const SOURCE_URL = 'https://github.com/a-ghorbani/xebench/blob/main/METHODOLOGY.md';
const SUPPORTED_PROTOCOLS = new Set(['cpu-reference-v1']); // schemaVersion 1 sessions

const round1 = (n) => (n == null ? null : Math.round(n * 10) / 10);

// Marketing names for lab devices (raw ro.product.model -> friendly). Lab rows
// bypass the website's crowd device-name resolver, so map here.
const DEVICE_NAMES = {
  'Pixel 9': 'Google Pixel 9',
  'SM-S911B': 'Samsung Galaxy S23',
  'ONEPLUS A6003': 'OnePlus 6',
  '25102PCBEG': 'POCO F8 Ultra',
  '2511FPC34G': 'POCO X8 Pro',
  '22126RN91Y': 'Redmi Note 12',
};
const friendlyDevice = (mfr, model) => {
  if (DEVICE_NAMES[model]) return DEVICE_NAMES[model];
  return [mfr, model].filter(Boolean).join(' ').trim() || 'unknown';
};

/** Compose a human note capturing the load-bearing methodology caveats. */
function asStated(rec, n) {
  const parts = [];
  const isLlamaBench = /llama-bench/.test(rec.protocol?.coldDefinition || '');
  parts.push(`n=${n} ${isLlamaBench ? 'reps (llama-bench mean)' : 'cold-run median'}`);
  parts.push(`prompt ${rec.protocol.promptLabel}`);
  parts.push(`decode ${rec.protocol.maxDecodeTokens} tok`);
  // prefill provenance differs per engine — surface it, it's the fairness crux
  const pm = rec.coldRuns?.[0]?.prefillMethod;
  if (pm && pm !== 'engine-timings') {
    parts.push(`prefill ${pm} (no engine counter)`);
  }
  if (rec.sustained) {
    parts.push(`sustained ${rec.sustained.degradationPct?.toFixed(0)}% decode drop over ${Math.round(rec.sustained.durationSec)}s`);
  }
  if (rec.guardsPassed === null) parts.push(`GUARDS UNVERIFIED: ${rec.guardNotes?.join('; ')}`);
  else if (!rec.guardsPassed) parts.push(`GUARDS FAILED: ${rec.guardNotes?.join('; ')}`);
  return parts.join('; ');
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const nonEmpty = (v) => typeof v === 'string' && v.trim() !== '';
const positiveInt = (v) => Number.isInteger(v) && v >= 1;

/** Read every non-empty line of every input; any bad line rejects the run. */
function readRecords(paths) {
  const records = [];
  for (const file of paths) {
    let text;
    try {
      text = readFileSync(file, 'utf8');
    } catch (error) {
      throw new PublicationError(`cannot read ${file}: ${error.message}`);
    }
    text.split('\n').forEach((line, index) => {
      if (!line.trim()) return;
      const where = `${file}:${index + 1}`;
      let rec;
      try {
        rec = JSON.parse(line);
      } catch (error) {
        throw new PublicationError(`${where}: not valid JSON (malformed or truncated record): ${error.message}`);
      }
      if (!isObject(rec)) throw new PublicationError(`${where}: record is not a JSON object`);
      assertFinite(rec, where);
      if (rec.schema !== 'xebench-raw') {
        throw new PublicationError(`${where}: unexpected schema ${JSON.stringify(rec.schema)} (expected "xebench-raw")`);
      }
      records.push({ rec, where });
    });
  }
  return records;
}

/**
 * The sample count behind the published medians: the summary `n`, which must be
 * identical for every published metric and must match the successful
 * repetitions present in the record (where the record carries them).
 */
function sampleCount(rec, where, metrics, legacy, warnings) {
  const counts = new Set();
  for (const [name, agg] of metrics) {
    if (agg == null || agg.median == null) continue;
    if (!positiveInt(agg.n)) throw new PublicationError(`${where}: summary.${name}.n must be a positive integer`);
    counts.add(agg.n);
  }
  if (counts.size === 0) throw new PublicationError(`${where}: no published metric has a measured median`);
  if (counts.size > 1) {
    throw new PublicationError(`${where}: summary metrics disagree on n (${[...counts].join(', ')})`);
  }
  const [n] = counts;
  const runs = rec.coldRuns;
  if (!Array.isArray(runs)) throw new PublicationError(`${where}: coldRuns must be an array`);
  const llamaBench = legacy && /llama-bench/.test(rec.protocol.coldDefinition || '');
  if (llamaBench) {
    // llama-bench reports mean/stddev only; its repetitions are not in the record.
    warnings.push(`${where}: llama-bench record; n=${n} is self-reported (repetitions not present)`);
  } else {
    const successful = legacy ? runs : runs.filter((run) => isObject(run) && run.status === 'complete');
    for (const [name, agg] of metrics) {
      if (agg == null || agg.median == null) continue;
      const present = successful.filter((run) => isObject(run) && typeof run[name] === 'number').length;
      if (present !== n || successful.length !== n) {
        throw new PublicationError(
          `${where}: summary.${name}.n=${n} but the record holds ${present} of ${successful.length} successful repetitions with ${name}`,
        );
      }
    }
  }
  if (n > rec.protocol.nColdRuns) {
    throw new PublicationError(`${where}: n=${n} exceeds protocol.nColdRuns=${rec.protocol.nColdRuns}`);
  }
  return n;
}

function toRow({ rec, where }, warnings) {
  const legacy = !Object.hasOwn(rec, 'schemaVersion');
  if (!legacy) {
    if (rec.schemaVersion !== 1 || rec.status !== 'complete') {
      throw new PublicationError(
        `${where}: Only complete, supported sessions can be aggregated; inspect capture checkpoints separately`,
      );
    }
    if (!SUPPORTED_PROTOCOLS.has(rec.protocol?.version)) {
      throw new PublicationError(`${where}: unsupported protocol version ${JSON.stringify(rec.protocol?.version)}`);
    }
    if (update) {
      throw new PublicationError(
        `${where}: Reference sessions are preview-only until protocol validation and consumer migration are implemented`,
      );
    }
  } else {
    if (isObject(rec.protocol) && rec.protocol.version !== undefined) {
      throw new PublicationError(`${where}: legacy record (no schemaVersion) declares protocol.version; incompatible`);
    }
    if (update && !allowLegacy) {
      throw new PublicationError(
        `${where}: legacy record (no schemaVersion) carries no validated protocol evidence; ` +
          're-run with --allow-legacy to publish it explicitly',
      );
    }
  }

  for (const field of ['engine', 'backend', 'platform', 'model']) {
    if (!nonEmpty(rec[field])) throw new PublicationError(`${where}: missing ${field}`);
  }
  if (rec.quant != null && typeof rec.quant !== 'string') throw new PublicationError(`${where}: quant must be a string`);
  if (!isObject(rec.deviceInfo) || !nonEmpty(rec.deviceInfo.model)) {
    throw new PublicationError(`${where}: deviceInfo.model is required`);
  }
  if (typeof rec.timestampIso !== 'string' || !/^\d{4}-\d{2}/.test(rec.timestampIso)) {
    throw new PublicationError(`${where}: timestampIso must be an ISO date`);
  }
  const p = rec.protocol;
  if (!isObject(p) || !positiveInt(p.nColdRuns) || !nonEmpty(p.promptLabel) || !positiveInt(p.maxDecodeTokens)) {
    throw new PublicationError(`${where}: protocol needs positive integer nColdRuns/maxDecodeTokens and a promptLabel`);
  }
  if (!isObject(rec.summary)) throw new PublicationError(`${where}: summary is missing (no successful repetitions)`);
  if (legacy ? typeof rec.guardsPassed !== 'boolean' : rec.guardsPassed !== null) {
    throw new PublicationError(`${where}: guardsPassed must be ${legacy ? 'a boolean' : 'null'}`);
  }

  const summary = rec.summary;
  const memoryMetric = summary.deltaPssMb?.median != null ? 'deltaPssMb' : 'peakPssMb';
  const metrics = ['prefillTps', 'decodeTps', 'ttftMs', memoryMetric].map((name) => [name, summary[name] ?? null]);
  for (const [name, agg] of metrics) {
    if (agg === null) continue;
    if (!isObject(agg) || (agg.median !== null && typeof agg.median !== 'number')) {
      throw new PublicationError(`${where}: summary.${name} must be null or an aggregate with a numeric median`);
    }
    if (agg.median === null) continue;
    if (name.endsWith('Tps') ? agg.median <= 0 : name === 'ttftMs' && agg.median < 0) {
      throw new PublicationError(`${where}: summary.${name}.median=${agg.median} is not a valid measurement`);
    }
  }
  const n = sampleCount(rec, where, metrics, legacy, warnings);

  if (n < p.nColdRuns) {
    if (update) {
      throw new PublicationError(`${where}: incomplete session (${n}/${p.nColdRuns} repetitions); not publishable`);
    }
    warnings.push(`${where}: incomplete session (${n}/${p.nColdRuns} repetitions)`);
  }
  if (update && rec.guardsPassed !== true) {
    throw new PublicationError(`${where}: run-condition guards did not pass (${(rec.guardNotes ?? []).join('; ')}); not publishable`);
  }
  if (legacy) warnings.push(`${where}: LEGACY record (no schemaVersion); protocol and evidence are not validated`);

  const dev = rec.deviceInfo;
  return {
    engine: rec.engine,
    backend: rec.backend,
    platform: rec.platform,
    device: friendlyDevice(dev.manufacturer, dev.model),
    soc: dev.soc || undefined,
    model: rec.model,
    quant: rec.quant || undefined,
    ppTps: round1(summary.prefillTps?.median),
    tgTps: round1(summary.decodeTps?.median),
    ttftMs: round1(summary.ttftMs?.median),
    // deltaPss = engine+model+KV footprint, the number a user cares about
    memoryMb: round1(summary[memoryMetric]?.median),
    provenance: 'measured',
    sourceName: SOURCE_NAME,
    sourceUrl: SOURCE_URL,
    sourceDate: rec.timestampIso.slice(0, 7),
    sampleCount: n,
    asStated: asStated(rec, n),
  };
}

/** Write next to the target, flush, then rename over it: readers see old or new, never partial. */
function writeAtomic(target, text, expectedBefore) {
  const tmp = join(dirname(target), `.${randomUUID()}.benchmarks.json.tmp`);
  let fd;
  try {
    fd = openSync(tmp, 'wx', 0o644);
    writeSync(fd, text);
    fsyncSync(fd);
    closeSync(fd);
    fd = undefined;
    if (readFileSync(target, 'utf8') !== expectedBefore) {
      throw new PublicationError('data/benchmarks.json changed during aggregation; re-run');
    }
    renameSync(tmp, target);
  } catch (error) {
    if (fd !== undefined) closeSync(fd);
    try {
      unlinkSync(tmp);
    } catch {
      // the temp file was never created or was already renamed
    }
    throw error;
  }
}

function main() {
  const schema = readJson(SCHEMA, 'data/benchmarks.schema.json');
  const rowSchema = schema.$defs?.row;
  if (!isObject(rowSchema)) throw new PublicationError('data/benchmarks.schema.json lacks $defs.row');

  // Validate the canonical file before touching any input, so --update can never
  // replace unreadable data with a partial envelope.
  let before;
  let envelope;
  if (update) {
    try {
      before = readFileSync(DATA, 'utf8');
    } catch (error) {
      throw new PublicationError(`cannot read data/benchmarks.json: ${error.message}`);
    }
    envelope = readJson(DATA, 'data/benchmarks.json');
    assertBenchmarks(envelope, schema, 'existing data/benchmarks.json');
  }

  const records = readRecords(files);
  if (records.length === 0) throw new PublicationError('no xebench-raw records found in the input');
  const warnings = [];
  const rows = records.map((record) => {
    const row = JSON.parse(JSON.stringify(toRow(record, warnings))); // drop undefined fields
    assertSchema(row, rowSchema, `${record.where}: aggregated row`, schema);
    return { row, where: record.where };
  });
  rows.sort((a, b) => compareRows(a.row, b.row) || (a.where < b.where ? -1 : a.where > b.where ? 1 : 0));

  // Two inputs for one row key is ambiguous: refuse to pick one implicitly.
  const byInputKey = new Map();
  for (const { row, where } of rows) {
    const key = rowKey(row);
    byInputKey.set(key, [...(byInputKey.get(key) ?? []), where]);
  }
  const collisions = [...byInputKey].filter(([, wheres]) => wheres.length > 1);
  for (const [key, wheres] of collisions) {
    const message = `${wheres.length} input records map to row key ${key}: ${wheres.join(', ')}`;
    if (update) throw new PublicationError(`${message}; pass exactly one record per row`);
    warnings.push(message);
  }
  for (const warning of warnings) console.error(`warning: ${warning}`);

  if (!update) {
    process.stdout.write(JSON.stringify(rows.map(({ row }) => row), null, pretty ? 2 : 0) + '\n');
    console.error(`aggregated ${rows.length} measured rows from ${files.length} file(s)`);
    return;
  }

  // Upsert: an input row replaces the canonical row with the same key (re-runs
  // overwrite in place); every other row (vendor citations, other devices) is kept.
  const byKey = new Map(envelope.rows.map((r) => [rowKey(r), r]));
  const replaced = [];
  for (const { row } of rows) {
    if (byKey.has(rowKey(row))) replaced.push(rowKey(row));
    byKey.set(rowKey(row), row);
  }
  const merged = [...byKey.values()].sort(compareRows);
  const out = { ...envelope, generatedAt: new Date().toISOString().slice(0, 10), rowCount: merged.length, rows: merged };
  const text = JSON.stringify(out, null, 2) + '\n';
  assertBenchmarks(JSON.parse(text), schema, 'updated data/benchmarks.json');
  writeAtomic(DATA, text, before);
  for (const key of replaced) console.error(`replaced existing row ${key}`);
  console.error(
    `merged ${rows.length} measured rows into data/benchmarks.json ` +
      `(+${rows.length - replaced.length} new, ${replaced.length} replaced; ${merged.length} total). Commit + push to publish.`,
  );
}

try {
  main();
} catch (error) {
  if (!(error instanceof PublicationError)) throw error;
  console.error(`error: ${error.message}`);
  console.error(update ? 'data/benchmarks.json was not modified.' : 'no rows were emitted.');
  process.exit(1);
}
