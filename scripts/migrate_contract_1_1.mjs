#!/usr/bin/env node
/**
 * migrate_contract_1_1.mjs — migrate data/benchmarks.json from contract 1.0 to 1.1.
 *
 *   node scripts/migrate_contract_1_1.mjs           # migrate in place (atomic), or no-op if already 1.1
 *   node scripts/migrate_contract_1_1.mjs --check   # exit 1 if a migration is needed; never writes
 *
 * The migration sets schemaVersion "1.1", re-stamps generatedAt (ISO-8601 UTC)
 * and appends validation "legacy" to every provenance=measured row that has no
 * validation. No other field changes; vendor/community rows stay byte-identical.
 * It is idempotent: on an already-migrated file it writes nothing.
 *
 * Fail-closed: the input and the result are schema- and invariant-validated;
 * on any error it exits 1 and leaves the file byte-identical.
 * Exit codes: 0 ok (or nothing to do), 1 error or (--check) migration needed, 2 usage.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  assertBenchmarks, migrateTo11, migrationNeeds, PublicationError, readJson, writeAtomic,
} from './lib/publication.mjs';

const args = process.argv.slice(2);
const check = args.includes('--check');
if (args.some((a) => a !== '--check')) {
  console.error('usage: node scripts/migrate_contract_1_1.mjs [--check]');
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

  if (check) {
    const needs = migrationNeeds(envelope);
    if (needs.length) {
      console.error(`data/benchmarks.json needs the 1.1 migration: ${needs.join('; ')}`);
      process.exit(1);
    }
    console.error('data/benchmarks.json is at contract 1.1');
    process.exit(0);
  }

  const { envelope: out, touched, changed } = migrateTo11(envelope);
  if (!changed) {
    console.error('data/benchmarks.json is already at contract 1.1; nothing to do');
    process.exit(0);
  }
  const text = JSON.stringify(out, null, 2) + '\n';
  assertBenchmarks(JSON.parse(text), schema, 'migrated data/benchmarks.json');
  writeAtomic(DATA, text, before);
  console.error(
    `migrated data/benchmarks.json ${envelope.schemaVersion} -> ${out.schemaVersion}: ` +
      `${touched} measured row(s) labelled validation=legacy, ${out.rows.length - touched} unchanged`,
  );
} catch (error) {
  console.error(error instanceof PublicationError ? `error: ${error.message}` : error);
  console.error('data/benchmarks.json was not modified.');
  process.exit(1);
}
