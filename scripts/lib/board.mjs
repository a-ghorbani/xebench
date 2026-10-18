/**
 * The board configuration (configs/board.json): the run configurations whose sessions the
 * board displays. A row published from an evidence bundle names its configuration
 * (configurationId); only sessions of a listed configuration are displayed, and
 * aggregate.mjs --update refuses to publish any other unless --allow-unlisted-configuration
 * is given (such a session is kept in the history and never displayed).
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { CONFIGURATION_ID, PublicationError } from './publication.mjs';

export const BOARD_SCHEMA = 'xebench-board';
export const BOARD_FILE = 'configs/board.json';
const FIELDS = ['schema', 'schemaVersion', 'description', 'displayConfigurations'];

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/** The ids of the committed, non-experiment run configurations under xebenchapp/configs/. */
export function reviewedConfigurationIds(root) {
  const ids = new Set();
  for (const name of readdirSync(join(root, 'xebenchapp/configs')).sort()) {
    if (!name.endsWith('.json')) continue;
    const id = JSON.parse(readFileSync(join(root, 'xebenchapp/configs', name), 'utf8')).id;
    if (typeof id === 'string' && !id.startsWith('experiment-')) ids.add(id);
  }
  return ids;
}

/** Errors in a parsed board configuration, as "path: message" strings. */
export function boardErrors(board, reviewedIds) {
  if (!isObject(board)) return [`${BOARD_FILE}: not a JSON object`];
  const errors = [];
  for (const key of Object.keys(board)) if (!FIELDS.includes(key)) errors.push(`${BOARD_FILE}: unknown field ${key}`);
  if (board.schema !== BOARD_SCHEMA) errors.push(`${BOARD_FILE}: schema must be ${BOARD_SCHEMA}`);
  if (board.schemaVersion !== 1) errors.push(`${BOARD_FILE}: schemaVersion must be 1`);
  if (typeof board.description !== 'string' || !board.description) errors.push(`${BOARD_FILE}: description must be a non-empty string`);
  const ids = board.displayConfigurations;
  if (!Array.isArray(ids) || !ids.length) {
    errors.push(`${BOARD_FILE}: displayConfigurations must be a non-empty array`);
    return errors;
  }
  if (new Set(ids).size !== ids.length) errors.push(`${BOARD_FILE}: displayConfigurations has a duplicate id`);
  for (const id of ids) {
    if (typeof id !== 'string' || !CONFIGURATION_ID.test(id)) {
      errors.push(`${BOARD_FILE}: ${JSON.stringify(id)} is not a configuration id`);
    } else if (!reviewedIds.has(id)) {
      errors.push(`${BOARD_FILE}: ${id} is not a committed, non-experiment configuration under xebenchapp/configs/`);
    }
  }
  return errors;
}

/** The validated board configuration of the repository at `root`; throws on any error. */
export function loadBoard(root) {
  let board;
  try {
    board = JSON.parse(readFileSync(join(root, BOARD_FILE), 'utf8'));
  } catch (error) {
    throw new PublicationError(`${BOARD_FILE}: unreadable (${error.code ?? error.name})`);
  }
  const errors = boardErrors(board, reviewedConfigurationIds(root));
  if (errors.length) throw new PublicationError(errors.join('\n  '));
  return { displayConfigurations: [...board.displayConfigurations] };
}

/** True when the board displays sessions of this row's configuration. */
export const displayable = (board, row) => board.displayConfigurations.includes(row?.configurationId);
