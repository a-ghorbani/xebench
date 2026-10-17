#!/usr/bin/env node
/**
 * validate_data.mjs — check published files against their schemas and contract invariants.
 *
 *   node scripts/validate_data.mjs                    # data/benchmarks.json and data/findings.json
 *   node scripts/validate_data.mjs <benchmarks.json> ...   # any benchmarks envelopes (e.g. fixtures)
 *
 * Exits 1 with the first failing file's errors; 0 when every file conforms.
 */
import { assertBenchmarks, assertFindings, PublicationError, readJson } from './lib/publication.mjs';

const data = (name) => new URL(`../data/${name}`, import.meta.url);
const paths = process.argv.slice(2);

try {
  const schema = readJson(data('benchmarks.schema.json'), 'data/benchmarks.schema.json');
  if (paths.length) {
    for (const path of paths) assertBenchmarks(readJson(path, path), schema, path);
    console.error(`${paths.join(', ')} ${paths.length === 1 ? 'conforms' : 'conform'} to the benchmarks contract`);
  } else {
    assertBenchmarks(readJson(data('benchmarks.json'), 'data/benchmarks.json'), schema, 'data/benchmarks.json');
    assertFindings(readJson(data('findings.json'), 'data/findings.json'),
      readJson(data('findings.schema.json'), 'data/findings.schema.json'), 'data/findings.json');
    console.error('data/benchmarks.json and data/findings.json conform to their schemas');
  }
} catch (error) {
  if (!(error instanceof PublicationError)) throw error;
  console.error(`error: ${error.message}`);
  process.exit(1);
}
