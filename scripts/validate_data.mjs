#!/usr/bin/env node
/**
 * validate_data.mjs — check published files against their schemas and contract invariants.
 *
 *   node scripts/validate_data.mjs                    # data/benchmarks.json, data/sessions/index.json, data/findings.json
 *   node scripts/validate_data.mjs <benchmarks.json> ...   # any benchmarks envelopes (e.g. fixtures)
 *
 * The default run also checks the published-session history against the benchmarks
 * file: every verified row and every row with evidence is backed by a published
 * session, every published session reproduces its row from its bundle under evidence/,
 * and applying configs/board.json (scripts/lib/sets.mjs) changes nothing: each engine
 * displays only its declared measurement set, retired keys are not displayed, and no
 * failed row is.
 *
 * Exits 1 with the first failing file's errors; 0 when every file conforms.
 */
import { assertBenchmarks, assertFindings, PublicationError, readJson } from './lib/publication.mjs';
import { fileURLToPath } from 'node:url';
import { reproductionErrors } from './lib/bundles.mjs';
import { assertIndex } from './lib/history.mjs';
import { boardExists, loadBoard } from './lib/board.mjs';
import { boardDisplayErrors } from './lib/sets.mjs';

const data = (name) => new URL(`../data/${name}`, import.meta.url);
const paths = process.argv.slice(2);

try {
  const schema = readJson(data('benchmarks.schema.json'), 'data/benchmarks.schema.json');
  if (paths.length) {
    for (const path of paths) assertBenchmarks(readJson(path, path), schema, path);
    console.error(`${paths.join(', ')} ${paths.length === 1 ? 'conforms' : 'conform'} to the benchmarks contract`);
  } else {
    const envelope = readJson(data('benchmarks.json'), 'data/benchmarks.json');
    assertBenchmarks(envelope, schema, 'data/benchmarks.json');
    const schemas = { benchmarks: schema, row: schema.$defs.row,
      index: readJson(data('sessions/index.schema.json'), 'data/sessions/index.schema.json') };
    const index = readJson(data('sessions/index.json'), 'data/sessions/index.json');
    const root = fileURLToPath(new URL('..', import.meta.url));
    assertIndex(index, envelope, schemas);
    // configs/board.json decides what is displayed; it is checked whenever it exists, and
    // needed as soon as a session is published.
    if (boardExists(root)) {
      const errors = boardDisplayErrors(envelope, index, loadBoard(root));
      if (errors.length) {
        throw new PublicationError(`data/benchmarks.json and data/sessions/index.json disagree with configs/board.json:\n  ${errors.slice(0, 20).join('\n  ')}`);
      }
    } else if (index.keys.length) {
      throw new PublicationError('data/sessions/index.json holds published sessions, so configs/board.json is required');
    }
    // Every published session still comes out of its hosted bundle as the row the index keeps.
    const unreproduced = reproductionErrors(index, fileURLToPath(new URL('..', import.meta.url)));
    if (unreproduced.length) {
      throw new PublicationError(`data/sessions/index.json does not reproduce from evidence/:\n  ${unreproduced.slice(0, 20).join('\n  ')}`);
    }
    assertFindings(readJson(data('findings.json'), 'data/findings.json'),
      readJson(data('findings.schema.json'), 'data/findings.schema.json'), 'data/findings.json');
    console.error('data/benchmarks.json, data/sessions/index.json and data/findings.json conform to their schemas');
  }
} catch (error) {
  if (!(error instanceof PublicationError)) throw error;
  console.error(`error: ${error.message}`);
  process.exit(1);
}
