#!/usr/bin/env node
/**
 * aggregate.mjs — publish EngineBenchmarkRow[] (pocketpal-website/lib/engines/types.ts,
 * data/benchmarks.schema.json) into data/benchmarks.json.
 *
 * Two inputs:
 *
 *   Evidence bundles (scripts/export_evidence.py output, hosted under evidence/<sha>/):
 *     node scripts/aggregate.mjs --from-bundles evidence/ --dry-run [--json]
 *         report every session: the row it gives, its publication-policy-v1 validation,
 *         and what --update would change on the board. Writes nothing.
 *     node scripts/aggregate.mjs --from-bundles evidence/ --session <id> [...] --update
 *         [--replace-legacy] [--replace-failed]
 *         publish the named sessions. Each bundle is verified and its evidence re-checked
 *         by scripts/publication_policy.py, which also decides validation and guardsPassed
 *         (docs/RUN-CONDITIONS.md, Publication policy v1). Statistics are recomputed from
 *         the bundle's repetitions; a record summary that disagrees is refused. Every
 *         published session is kept in data/sessions/index.json, which also records the
 *         row each key displays (scripts/lib/history.mjs, SELECTION_RULE). A row is
 *         verified only through this path.
 *
 *   Legacy raw JSONL (one record per line, pulled off-device):
 *     node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]          # preview rows on stdout
 *     node scripts/aggregate.mjs <raw.jsonl> [...] --update --allow-legacy
 *         upsert reviewed legacy rows (validation "legacy"). schemaVersion 1 sessions
 *         preview as "unverified" or "failed"; they are published only from bundles.
 *
 * Fail-closed: any malformed, truncated, non-finite, unsupported, tampered or
 * inconsistent input aborts the whole run with exit code 1 and no output/write. With
 * --update, the existing canonical files (data/benchmarks.json, data/sessions/index.json)
 * and the result are validated, and the files are replaced together atomically or left
 * byte-identical on any failure.
 *
 * An existing row with validation "failed" (e.g. invalidated by a later correctness
 * re-check) is never overwritten or replaced without --replace-failed, and a legacy row
 * is never replaced by a bundle session without --replace-legacy. Each replacement is
 * logged, and a row a bundle session replaces is kept in data/sessions/index.json.
 * Experiment sessions, bug reproductions and sessions without a measured repetition never
 * become rows.
 *
 * It does NOT invent numbers: every field comes from the record or its bundle. --update
 * writes schemaVersion 1.1 with an ISO-8601 UTC generatedAt, or 1.2 once a row carries
 * build.runtimes (contract 1.2 build identity, from a session's runtimeVersions).
 */
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  assertBenchmarks, assertFinite, assertSchema, buildFromSession, compareRows, CONTRACT_VERSION, contractVersionFor,
  friendlyDevice, isoNow, migrationNeeds, PublicationError, readJson, rowInvariantErrors, rowKey, writeAtomicGroup,
} from './lib/publication.mjs';
import { bundleRow, isHosted, listBundles, readBoundBundle, reproductionErrors, runPolicy } from './lib/bundles.mjs';
import { assertIndex, planPublication } from './lib/history.mjs';

const USAGE =
  'usage:\n' +
  '  node scripts/aggregate.mjs --from-bundles <dir> --dry-run [--session <id>]... [--json]\n' +
  '        # report what the evidence bundles in <dir> would publish; writes nothing\n' +
  '  node scripts/aggregate.mjs --from-bundles <dir> --session <id> [--session <id>]... --update\n' +
  '        [--replace-legacy] [--replace-failed]       # publish those sessions (bundles under evidence/<sha>/)\n' +
  '  node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]                  # preview EngineBenchmarkRow[] on stdout\n' +
  '  node scripts/aggregate.mjs <raw.jsonl> [...] --update --allow-legacy    # upsert reviewed legacy rows into data/benchmarks.json\n' +
  '  ... --update [--allow-legacy] --replace-failed                          # also replace rows marked validation=failed (logged)\n' +
  'exit codes: 0 ok, 1 rejected input/canonical data (nothing written), 2 usage';

const FLAGS = new Set(['--pretty', '--update', '--allow-legacy', '--replace-failed', '--replace-legacy', '--dry-run',
  '--json']);
const VALUED = new Set(['--from-bundles', '--session']);
const args = process.argv.slice(2);
const sessionArgs = [];
const files = [];
const unknown = [];
let fromBundles = null;
for (let index = 0; index < args.length; index += 1) {
  const arg = args[index];
  if (VALUED.has(arg)) {
    const value = args[index + 1];
    if (value === undefined || value.startsWith('--')) {
      unknown.push(`${arg} (needs a value)`);
      continue;
    }
    index += 1;
    if (arg === '--session') sessionArgs.push(value);
    else if (fromBundles !== null) unknown.push('--from-bundles (given twice)');
    else fromBundles = value;
  } else if (arg.startsWith('--')) {
    if (!FLAGS.has(arg)) unknown.push(arg);
  } else {
    files.push(arg);
  }
}
const has = (flag) => args.includes(flag);
const pretty = has('--pretty');
const update = has('--update'); // write data/benchmarks.json (from bundles, data/sessions/index.json too)
const allowLegacy = has('--allow-legacy');
const replaceFailed = has('--replace-failed');
const replaceLegacy = has('--replace-legacy');
const dryRun = has('--dry-run');
const asJson = has('--json');
const usage = [];
if (unknown.length) usage.push(`unknown option(s): ${unknown.join(' ')}`);
if (fromBundles !== null) {
  if (files.length) usage.push('--from-bundles takes no raw JSONL files');
  if (dryRun === update) usage.push('--from-bundles needs exactly one of --dry-run or --update');
  if (update && !sessionArgs.length) usage.push('--update from bundles needs at least one --session <id>');
  if (allowLegacy || pretty) usage.push('--allow-legacy and --pretty apply to raw JSONL input only');
  if (asJson && !dryRun) usage.push('--json only applies to --dry-run');
} else {
  if (files.length === 0) usage.push('no input files');
  if (sessionArgs.length || dryRun || asJson || replaceLegacy) {
    usage.push('--session, --dry-run, --json and --replace-legacy apply to --from-bundles only');
  }
  if (allowLegacy && !update) usage.push('--allow-legacy only applies to --update');
}
if (replaceFailed && !update) usage.push('--replace-failed only applies to --update');
if (replaceLegacy && !update && fromBundles !== null) usage.push('--replace-legacy only applies to --update');
if (usage.length) {
  for (const line of usage) console.error(line);
  console.error(USAGE);
  process.exit(2);
}

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const DATA = fileURLToPath(new URL('../data/benchmarks.json', import.meta.url));
const SCHEMA = fileURLToPath(new URL('../data/benchmarks.schema.json', import.meta.url));
const INDEX = fileURLToPath(new URL('../data/sessions/index.json', import.meta.url));
const INDEX_SCHEMA = fileURLToPath(new URL('../data/sessions/index.schema.json', import.meta.url));
const SOURCE_NAME = 'PocketPal R&D lab (xebench)';
const SOURCE_URL = 'https://github.com/a-ghorbani/xebench/blob/main/METHODOLOGY.md';
const SUPPORTED_PROTOCOLS = new Set(['cpu-reference-v1']); // schemaVersion 1 sessions in raw preview
// Legacy producers that exported medians but not per-repetition values. Their
// summary n cannot be checked against repetitions; any other legacy record must
// carry its repetitions.
const SELF_REPORTED_N = new Map([
  ['xebench-0.4-app', 'legacy app record: n is the configured run count; repetitions not exported'],
  ['xebench-0.2-native', 'llama-bench record: n is self-reported; repetitions not exported'],
]);

const round1 = (n) => (n == null ? null : Math.round(n * 10) / 10);

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
    if (text.startsWith('﻿')) text = text.slice(1);
    const before = records.length;
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
    if (records.length === before) throw new PublicationError(`${file}: no records (empty or truncated file)`);
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
  const published = metrics.filter(([, agg]) => agg != null && agg.median != null).map(([name]) => name);
  const carriesRepetitions = runs.some((run) => isObject(run) && published.some((name) => name in run));
  if (legacy && !carriesRepetitions) {
    if (!SELF_REPORTED_N.has(rec.harnessVersion)) {
      throw new PublicationError(
        `${where}: legacy record without per-repetition values from unknown harnessVersion ` +
          `${JSON.stringify(rec.harnessVersion)}; n=${n} cannot be verified`,
      );
    }
    warnings.push(`${where}: ${SELF_REPORTED_N.get(rec.harnessVersion)} (n=${n})`);
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
  // EXPERIMENT ONLY records (docs/experiments/charging-ab.md) never become rows, in
  // preview or --update, whatever their protocol: readiness did not wait for charging.
  if (rec.chargingPolicy?.readiness === 'record-only-experiment' ||
      (isObject(rec.configuration) && Object.hasOwn(rec.configuration, 'experimentChargingReadiness'))) {
    throw new PublicationError(
      `${where}: experiment-only record (record-only-experiment charging readiness) is never publishable`,
    );
  }
  // Experiment configurations are named experiment-*; a record from one is never a row,
  // even if its experiment markers were removed.
  if (isObject(rec.configuration) && typeof rec.configuration.id === 'string' &&
      rec.configuration.id.startsWith('experiment-')) {
    throw new PublicationError(`${where}: record from an experiment configuration (experiment-*) is never publishable`);
  }
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
        `${where}: Reference sessions are preview-only as raw records; publish them from their sanitized ` +
          'evidence bundle with --from-bundles',
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
  // Legacy app records (xebench-0.4-app) carry no timestamp: sourceDate stays unknown.
  if (rec.timestampIso !== undefined && (typeof rec.timestampIso !== 'string' || !/^\d{4}-\d{2}/.test(rec.timestampIso))) {
    throw new PublicationError(`${where}: timestampIso must be an ISO date`);
  }
  const p = rec.protocol;
  if (!isObject(p) || !positiveInt(p.nColdRuns) || !nonEmpty(p.promptLabel) || !positiveInt(p.maxDecodeTokens)) {
    throw new PublicationError(`${where}: protocol needs positive integer nColdRuns/maxDecodeTokens and a promptLabel`);
  }
  if (!isObject(rec.summary)) throw new PublicationError(`${where}: summary is missing (no successful repetitions)`);
  // Raw reference sessions never claim a pass: null = unverified, false = an observed
  // condition failure was flagged (observed-conditions-v1, docs/RUN-CONDITIONS.md).
  if (legacy ? typeof rec.guardsPassed !== 'boolean' : rec.guardsPassed !== null && rec.guardsPassed !== false) {
    throw new PublicationError(`${where}: guardsPassed must be ${legacy ? 'a boolean' : 'null or false'}`);
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
  // Raw reference sessions never claim a pass (checked above): false = failed, null = unverified.
  const contract = legacy
    ? { validation: 'legacy' }
    : {
      validation: rec.guardsPassed === false ? 'failed' : 'unverified',
      guardsPassed: rec.guardsPassed,
      protocolVersion: rec.protocol.version,
      conditionPolicy: nonEmpty(rec.conditionPolicy?.version) ? rec.conditionPolicy.version : undefined,
      // Contract 1.2 build (engine, binding and runtime versions), from the session's
      // runtimeVersions when the app recorded them.
      build: buildFromSession(rec, where),
    };
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
    sourceDate: rec.timestampIso?.slice(0, 7),
    sampleCount: n,
    asStated: asStated(rec, n),
    ...contract,
  };
}

/** Producer-side contract check for a raw-record row: raw input never gives a verified row. */
function assertEmittable(row, label) {
  const errors = rowInvariantErrors(row, label);
  if (row.validation === 'verified') {
    errors.push(`${label}: refusing to emit validation=verified from a raw record; only evidence bundles verify rows`);
  }
  if (errors.length) throw new PublicationError(errors.join('\n  '));
}

/** Schemas of every published file. */
function loadSchemas() {
  const benchmarks = readJson(SCHEMA, 'data/benchmarks.schema.json');
  if (!isObject(benchmarks.$defs?.row)) throw new PublicationError('data/benchmarks.schema.json lacks $defs.row');
  return { benchmarks, row: benchmarks.$defs.row, index: readJson(INDEX_SCHEMA, 'data/sessions/index.schema.json') };
}

const readText = (path, label) => {
  try {
    return readFileSync(path, 'utf8');
  } catch (error) {
    throw new PublicationError(`cannot read ${label}: ${error.message}`);
  }
};

/**
 * The canonical files, validated before any input is read, so an update can never
 * replace unreadable data with a partial envelope or desynchronise the history.
 */
function loadCanonical(schemas) {
  const before = readText(DATA, 'data/benchmarks.json');
  const envelope = readJson(DATA, 'data/benchmarks.json');
  assertBenchmarks(envelope, schemas.benchmarks, 'existing data/benchmarks.json');
  const needs = migrationNeeds(envelope);
  if (needs.length) {
    throw new PublicationError(
      `existing data/benchmarks.json is not at contract ${CONTRACT_VERSION} (${needs.join('; ')}); ` +
        'run node scripts/migrate_contract_1_1.mjs first',
    );
  }
  const indexBefore = readText(INDEX, 'data/sessions/index.json');
  const index = readJson(INDEX, 'data/sessions/index.json');
  assertIndex(index, envelope, schemas, 'existing data/sessions/index.json');
  const unreproduced = reproductionErrors(index, ROOT);
  if (unreproduced.length) {
    throw new PublicationError(`existing data/sessions/index.json does not reproduce from the hosted bundles:\n  ${unreproduced.slice(0, 20).join('\n  ')}`);
  }
  return { before, envelope, indexBefore, index };
}

const serialize = (value) => JSON.stringify(value, null, 2) + '\n';

function aggregateRaw() {
  const schemas = loadSchemas();
  const canonical = update ? loadCanonical(schemas) : null;

  const records = readRecords(files);
  if (records.length === 0) throw new PublicationError('no xebench-raw records found in the input');
  const warnings = [];
  const rows = records.map((record) => {
    const row = JSON.parse(JSON.stringify(toRow(record, warnings))); // drop undefined fields
    assertSchema(row, schemas.row, `${record.where}: aggregated row`, schemas.benchmarks);
    assertEmittable(row, `${record.where}: aggregated row`);
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
  const { before, envelope, index } = canonical;
  const indexed = new Set(index.keys.map((entry) => entry.key));
  const byKey = new Map(envelope.rows.map((r) => [rowKey(r), r]));
  const replaced = [];
  const replacedFailed = [];
  for (const { row, where } of rows) {
    const key = rowKey(row);
    if (indexed.has(key)) {
      throw new PublicationError(
        `${where}: row ${key} is published from evidence bundles (data/sessions/index.json); a raw record cannot replace it`,
      );
    }
    const existing = byKey.get(key);
    if (existing?.validation === 'failed') {
      // A failed row records that a published number was invalidated; a re-run of
      // the original record must not silently restore it as a normal measurement.
      if (!replaceFailed) {
        throw new PublicationError(
          `${where}: refusing to overwrite row ${key}, which is marked validation=failed ` +
            `(incoming validation=${row.validation}); pass --replace-failed to replace it explicitly`,
        );
      }
      replacedFailed.push(`${key} (validation failed -> ${row.validation})`);
    }
    if (existing) replaced.push(key);
    byKey.set(key, row);
  }
  const merged = [...byKey.values()].sort(compareRows);
  const out = {
    ...envelope, schemaVersion: contractVersionFor(merged, envelope.schemaVersion), generatedAt: isoNow(),
    rowCount: merged.length, rows: merged,
  };
  const text = serialize(out);
  const parsed = JSON.parse(text);
  assertBenchmarks(parsed, schemas.benchmarks, 'updated data/benchmarks.json');
  assertIndex(index, parsed, schemas, 'data/sessions/index.json against the updated data/benchmarks.json');
  writeAtomicGroup([{ target: DATA, text, before }]);
  for (const key of replaced) console.error(`replaced existing row ${key}`);
  for (const entry of replacedFailed) console.error(`--replace-failed: replaced failed row ${entry}`);
  console.error(
    `merged ${rows.length} measured rows into data/benchmarks.json ` +
      `(+${rows.length - replaced.length} new, ${replaced.length} replaced; ${merged.length} total). Commit + push to publish.`,
  );
}

// ── Evidence bundles ─────────────────────────────────────────────────────────

const HEX64 = /^[0-9a-f]{64}$/;

/**
 * Verify every bundle and build each publishable session's row. In --update mode the
 * first invalid bundle aborts the run; --dry-run collects every problem and reports it.
 */
function readBundles(schemas) {
  const bundles = [];
  const sessions = [];
  for (const dir of listBundles(fromBundles)) {
    const name = basename(dir);
    const bundle = { dir, sha: HEX64.test(name) ? name : null, valid: false, errors: [], sessions: 0 };
    bundles.push(bundle);
    try {
      const report = runPolicy(dir, bundle.sha ?? undefined);
      if (!report.valid) throw new PublicationError(`${dir}: bundle refused:\n  ${report.errors.slice(0, 20).join('\n  ')}`);
      bundle.sha = report.bundleSha256;
      const bound = readBoundBundle(dir, bundle.sha);
      const hosted = isHosted(dir, ROOT, bundle.sha);
      const found = [];
      for (const item of report.sessions) {
        const doc = bound.file(`sessions/${item.sessionId}.json`);
        if (doc.sessionId !== item.sessionId || doc.captureId !== item.captureId) {
          throw new PublicationError(`${dir}: session ${item.sessionId} does not match the verified report`);
        }
        const capture = bound.file(`captures/${doc.captureId}.json`);
        const entry = {
          bundleSha256: bundle.sha, dir, hosted, sessionId: item.sessionId, timestampIso: doc.record.timestampIso,
          protocolVersion: doc.record.protocol.version, decision: item.decision, facts: item.facts, row: null,
        };
        if (item.decision.publishable) {
          const row = bundleRow({ doc, capture, decision: item.decision, facts: item.facts, bundleSha256: bundle.sha });
          if (row.sampleCount !== item.facts.measured) {
            throw new PublicationError(`${dir}: ${item.sessionId}: recomputed n ${row.sampleCount} differs from the verifier's ${item.facts.measured}`);
          }
          const label = `${dir}: ${item.sessionId}: bundle row`;
          assertSchema(row, schemas.row, label, schemas.benchmarks);
          const errors = rowInvariantErrors(row, label);
          if (errors.length) throw new PublicationError(errors.join('\n  '));
          entry.row = row;
        }
        found.push(entry);
      }
      bundle.valid = true;
      bundle.sessions = found.length;
      sessions.push(...found);
    } catch (error) {
      if (!(error instanceof PublicationError) || update) throw error;
      bundle.errors.push(error.message);
    }
  }
  return { bundles, sessions };
}

/** The candidates --update publishes: exactly the named sessions, each publishable and hosted. */
function namedCandidates(sessions) {
  const candidates = [];
  for (const id of [...new Set(sessionArgs)]) {
    const matches = sessions.filter((session) => session.sessionId === id);
    if (matches.length === 0) throw new PublicationError(`session ${id} is not in any bundle under ${fromBundles}`);
    if (matches.length > 1) {
      throw new PublicationError(`session ${id} is in ${matches.length} bundles (${matches.map((m) => m.bundleSha256.slice(0, 12)).join(', ')}); refusing to pick one`);
    }
    const [session] = matches;
    if (!session.decision.publishable) {
      throw new PublicationError(`session ${id} is never publishable: ${session.decision.refusal}`);
    }
    if (!session.hosted) {
      throw new PublicationError(
        `session ${id}: bundle ${session.bundleSha256} is not hosted at evidence/${session.bundleSha256}/ in this ` +
          'repository, so its evidence URL would not resolve',
      );
    }
    candidates.push(session);
  }
  return candidates;
}

const short = (sha) => (sha ? `${sha.slice(0, 8)}…` : '?');
const cell = (value) => String(value ?? '').replace(/\|/g, '\\|');
const openChecks = (decision) => decision.checks.filter((check) => check.outcome !== 'pass')
  .map((check) => `${check.check} ${check.outcome}: ${check.detail}`).join('; ') || '—';

function printDryRun(bundles, sessions, plan, planError, schemaVersion) {
  if (asJson) {
    process.stdout.write(JSON.stringify({
      bundles: bundles.map(({ dir, sha, valid, errors, sessions: count }) => ({ dir, bundleSha256: sha, valid, errors, sessions: count })),
      sessions: sessions.map(({ dir, ...rest }) => rest),
      plan: plan && { actions: plan.actions, needs: plan.needs, schemaVersion }, planError,
    }, null, 2) + '\n');
    return;
  }
  const lines = ['Dry run: nothing is written.', '', '| Bundle | Sessions | Verified | Hosted |', '| --- | --- | --- | --- |'];
  for (const bundle of bundles) {
    const hosted = bundle.sha && isHosted(bundle.dir, ROOT, bundle.sha) ? 'yes' : 'no';
    lines.push(`| ${short(bundle.sha)} | ${bundle.sessions} | ${bundle.valid ? 'yes' : `no: ${cell(bundle.errors.join(' '))}`} | ${hosted} |`);
  }
  lines.push('', '| Bundle | Session | Protocol | Row | n | pp / tg tok/s | Validation | Checks not passed |',
    '| --- | --- | --- | --- | --- | --- | --- | --- |');
  for (const session of sessions) {
    const { row, decision } = session;
    const label = row
      ? `${row.engine} ${row.backend} · ${row.device} · ${row.model}${row.quant ? ` ${row.quant}` : ''}`
      : `${session.facts.engine} ${session.facts.backend}`;
    const validation = decision.publishable ? decision.validation : `not publishable (${decision.refusal})`;
    lines.push(`| ${short(session.bundleSha256)} | ${session.sessionId} | ${session.protocolVersion} | ${cell(label)} | ` +
      `${row ? row.sampleCount : session.facts.measured} | ${row ? `${row.ppTps} / ${row.tgTps}` : '—'} | ${cell(validation)} | ` +
      `${cell(decision.publishable ? openChecks(decision) : '—')} |`);
  }
  lines.push('');
  if (planError) {
    lines.push(`Board plan: refused: ${planError}`);
  } else if (plan) {
    lines.push(`If every publishable session above were published (data/benchmarks.json schemaVersion ${schemaVersion}):`, '',
      '| Row key | Displayed session | Rule | Change | Needs |', '| --- | --- | --- | --- | --- |');
    for (const action of plan.actions) {
      lines.push(`| ${cell(action.key)} | ${action.displayed} | ${action.rule} | ${cell(action.change)} | ${action.needs.join(' ') || '—'} |`);
    }
  }
  process.stdout.write(lines.join('\n') + '\n');
}

function publishFromBundles() {
  const schemas = loadSchemas();
  const { before, envelope, indexBefore, index } = loadCanonical(schemas);
  const { bundles, sessions } = readBundles(schemas);

  if (dryRun) {
    const wanted = sessionArgs.length ? new Set(sessionArgs) : null;
    const missing = [...(wanted ?? [])].filter((id) => !sessions.some((session) => session.sessionId === id));
    if (missing.length) throw new PublicationError(`session(s) not in any bundle under ${fromBundles}: ${missing.join(', ')}`);
    const shown = wanted ? sessions.filter((session) => wanted.has(session.sessionId)) : sessions;
    // A session in more than one bundle is ambiguous; --update refuses it, so the plan leaves it out.
    const copies = new Map();
    for (const session of sessions) copies.set(session.sessionId, (copies.get(session.sessionId) ?? 0) + 1);
    for (const session of shown) {
      if (copies.get(session.sessionId) > 1 && session.decision.publishable) {
        session.decision = { ...session.decision, publishable: false,
          refusal: `in ${copies.get(session.sessionId)} bundles; --update refuses to pick one` };
        session.row = null;
      }
    }
    const candidates = shown.filter((session) => session.row);
    let plan = null;
    let planError = null;
    try {
      plan = planPublication(envelope, index, candidates);
    } catch (error) {
      if (!(error instanceof PublicationError)) throw error;
      planError = error.message;
    }
    const schemaVersion = plan ? contractVersionFor(plan.rows, envelope.schemaVersion) : null;
    printDryRun(bundles, shown, plan, planError, schemaVersion);
    const invalid = bundles.filter((bundle) => !bundle.valid);
    if (invalid.length) {
      throw new PublicationError(`${invalid.length} bundle(s) refused; see the table above`);
    }
    return;
  }

  const candidates = namedCandidates(sessions);
  const plan = planPublication(envelope, index, candidates);
  if (plan.actions.every((action) => action.added.length === 0)) {
    console.error('every named session is already published with identical content; nothing to write');
    return;
  }
  const given = new Set([...(replaceLegacy ? ['--replace-legacy'] : []), ...(replaceFailed ? ['--replace-failed'] : [])]);
  const missing = plan.actions.filter((action) => action.needs.some((flag) => !given.has(flag)));
  if (missing.length) {
    throw new PublicationError(missing.map((action) =>
      `row ${action.key}: ${action.change}; pass ${action.needs.join(' and ')} to replace it explicitly`).join('\n  '));
  }
  const out = {
    ...envelope, schemaVersion: contractVersionFor(plan.rows, envelope.schemaVersion), generatedAt: isoNow(),
    rowCount: plan.rows.length, rows: plan.rows,
  };
  const text = serialize(out);
  const indexText = serialize(plan.index);
  const parsed = JSON.parse(text);
  assertBenchmarks(parsed, schemas.benchmarks, 'updated data/benchmarks.json');
  assertIndex(JSON.parse(indexText), parsed, schemas, 'updated data/sessions/index.json');
  writeAtomicGroup([{ target: INDEX, text: indexText, before: indexBefore }, { target: DATA, text, before }]);
  for (const action of plan.actions) {
    const flags = action.needs.length ? ` [${action.needs.join(' ')}]` : '';
    console.error(`${action.key}: ${action.change}; displays ${action.displayed} (${action.rule})${flags}`);
  }
  console.error(
    `published ${candidates.length} session(s) into data/sessions/index.json and data/benchmarks.json ` +
      `(schemaVersion ${out.schemaVersion}, ${plan.rows.length} rows). Commit + push to publish.`,
  );
}

try {
  if (fromBundles !== null) publishFromBundles();
  else aggregateRaw();
} catch (error) {
  console.error(error instanceof PublicationError ? `error: ${error.message}` : error);
  if (error?.partialWrite) process.exit(1); // the message names the files left with new content
  console.error(update
    ? `data/benchmarks.json${fromBundles !== null ? ' and data/sessions/index.json were' : ' was'} not modified.`
    : dryRun ? 'nothing was written.' : 'no rows were emitted.');
  process.exit(1);
}
