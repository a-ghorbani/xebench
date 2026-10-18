#!/usr/bin/env node
/**
 * validate_data.mjs — check published files against their schemas and contract invariants.
 *
 *   node scripts/validate_data.mjs                    # data/benchmarks.json, data/sessions/index.json, data/findings.json
 *   node scripts/validate_data.mjs <benchmarks.json> ...   # any benchmarks envelopes (e.g. fixtures)
 *
 * The default run also checks the published-session history against the benchmarks
 * file: every verified row and every row with evidence is backed by a published
 * session, each key shows the row its selection rule picks (scripts/lib/history.mjs),
 * and every published session reproduces its row from its bundle under evidence/.
 *
 * Exits 1 with the first failing file's errors; 0 when every file conforms.
 */
import { assertBenchmarks, assertFindings, PublicationError, readJson } from './lib/publication.mjs';
import { fileURLToPath } from 'node:url';
import { reproductionErrors } from './lib/bundles.mjs';
import { assertIndex } from './lib/history.mjs';
import { BOARD_FILE, loadBoard } from './lib/board.mjs';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

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
    // configs/board.json decides which published sessions are displayed; it is validated
    // whenever it exists, and needed as soon as a session is published.
    const board = () => loadBoard(root);
    if (existsSync(join(root, BOARD_FILE))) board();
    assertIndex(index, envelope, schemas, board);
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
