#!/usr/bin/env node
/**
 * validate_data.mjs — check the checked-in published files against their schemas.
 *
 *   node scripts/validate_data.mjs            # validates data/benchmarks.json and data/findings.json
 *
 * Exits 1 with the first failing file's errors; 0 when both conform.
 */
import { assertBenchmarks, assertFindings, PublicationError, readJson } from './lib/publication.mjs';

const data = (name) => new URL(`../data/${name}`, import.meta.url);

try {
  assertBenchmarks(readJson(data('benchmarks.json'), 'data/benchmarks.json'),
    readJson(data('benchmarks.schema.json'), 'data/benchmarks.schema.json'), 'data/benchmarks.json');
  assertFindings(readJson(data('findings.json'), 'data/findings.json'),
    readJson(data('findings.schema.json'), 'data/findings.schema.json'), 'data/findings.json');
  console.error('data/benchmarks.json and data/findings.json conform to their schemas');
} catch (error) {
  if (!(error instanceof PublicationError)) throw error;
  console.error(`error: ${error.message}`);
  process.exit(1);
}
