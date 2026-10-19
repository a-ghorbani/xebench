/**
 * The board configuration (configs/board.json): for each engine, the one measurement set the
 * board displays, and the row keys retired from the board (docs/RUN-CONDITIONS.md,
 * Measurement sets).
 *
 * An engine's set is one of:
 *   {"set": "sessions", "configurations": [{"id": "...", "backends": ["cpu"]}, ...], "engineVersion": "...",
 *    "bindingVersion"?: "...", "measuredFrom": "YYYY-MM-DD",
 *    "carryover"?: {"measured": "YYYY-MM", "reason": "...", "keys": [...]}}
 *       sessions published from evidence bundles whose row names one of `configurations` and runs
 *       one of that configuration's `backends`, whose build carries exactly this engineVersion and
 *       bindingVersion (absent when not declared), measured on or after measuredFrom (UTC). The
 *       backends keep a configuration's other runs off the board: an accelerator configuration's
 *       CPU reference run has the same row key as a CPU board row and must never replace it. `carryover` lists rows without versions (measured
 *       in that month) that stay displayed until a session of the set replaces them or they are
 *       retired; it exists only to finish a campaign that started before sets were declared.
 *   {"set": "pre-version-recording", "measured": "YYYY-MM"}
 *       the engine's rows from before the app recorded engine versions (validation "legacy"),
 *       all measured in that month. They show no version.
 *
 * retiredKeys: [{"key", "reason", "retiredAt"}] — row keys the board no longer displays. The
 * reason stays in the data (here and in data/sessions/index.json), never on the page.
 *
 * Pure validation and lookups; scripts/lib/sets.mjs applies the configuration.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { CONFIGURATION_ID, PublicationError } from './publication.mjs';

export const BOARD_SCHEMA = 'xebench-board';
export const BOARD_VERSION = 2;
export const BOARD_FILE = 'configs/board.json';
export const SESSIONS = 'sessions';
export const PRE_VERSION = 'pre-version-recording';
const FIELDS = ['schema', 'schemaVersion', 'description', 'engines', 'retiredKeys'];
const SESSION_FIELDS = ['set', 'configurations', 'engineVersion', 'bindingVersion', 'measuredFrom', 'carryover'];
const PRE_VERSION_FIELDS = ['set', 'measured'];
const CARRYOVER_FIELDS = ['measured', 'reason', 'keys'];
const RETIRED_FIELDS = ['key', 'reason', 'retiredAt'];
const CONFIGURATION_FIELDS = ['id', 'backends'];
export const BACKENDS = Object.freeze(['cpu', 'gpu', 'npu', 'ane']);
const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value) => typeof value === 'string' && value.trim() !== '';
const isDay = (value) => typeof value === 'string' && DAY.test(value) &&
  new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const codepoint = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/** A row key: engine|backend|platform|device|model|quant|provenance, of a measured row. */
export const keyParts = (key) => {
  const parts = String(key).split('|');
  return parts.length === 7 && parts.every((part) => part !== '') && parts[6] === 'measured' ? parts : null;
};

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

function unknownFields(value, allowed, at, errors) {
  for (const key of Object.keys(value)) if (!allowed.includes(key)) errors.push(`${at}: unknown field ${key}`);
}

function keyListErrors(keys, at, engine, errors) {
  if (!Array.isArray(keys)) {
    errors.push(`${at} must be an array of row keys`);
    return;
  }
  keys.forEach((key, index) => {
    const parts = keyParts(key);
    if (!parts) errors.push(`${at}[${index}]: ${JSON.stringify(key)} is not a measured row key`);
    else if (engine && parts[0] !== engine) errors.push(`${at}[${index}]: ${key} is not a ${engine} row`);
    if (index > 0 && codepoint(keys[index - 1], key) >= 0) errors.push(`${at}: keys must be unique and sorted`);
  });
}

function engineErrors(engine, entry, reviewedIds, errors) {
  const at = `${BOARD_FILE}: engines.${engine}`;
  if (!isObject(entry)) {
    errors.push(`${at} must be an object`);
    return;
  }
  if (entry.set === PRE_VERSION) {
    unknownFields(entry, PRE_VERSION_FIELDS, at, errors);
    if (typeof entry.measured !== 'string' || !MONTH.test(entry.measured)) errors.push(`${at}.measured must be YYYY-MM`);
    return;
  }
  if (entry.set !== SESSIONS) {
    errors.push(`${at}.set must be "${SESSIONS}" or "${PRE_VERSION}"`);
    return;
  }
  unknownFields(entry, SESSION_FIELDS, at, errors);
  const configurations = entry.configurations;
  if (!Array.isArray(configurations) || !configurations.length) {
    errors.push(`${at}.configurations must be a non-empty array of {id, backends}`);
  } else {
    const ids = configurations.map((item) => item?.id);
    if (new Set(ids).size !== ids.length) errors.push(`${at}.configurations has a duplicate id`);
    configurations.forEach((item, index) => {
      const where = `${at}.configurations[${index}]`;
      if (!isObject(item)) {
        errors.push(`${where} must be an object {id, backends}`);
        return;
      }
      unknownFields(item, CONFIGURATION_FIELDS, where, errors);
      const { id, backends } = item;
      if (typeof id !== 'string' || !CONFIGURATION_ID.test(id)) {
        errors.push(`${where}: ${JSON.stringify(id)} is not a configuration id`);
      } else if (!reviewedIds.has(id)) {
        errors.push(`${where}: ${id} is not a committed, non-experiment configuration under xebenchapp/configs/`);
      }
      if (!Array.isArray(backends) || !backends.length || backends.some((backend) => !BACKENDS.includes(backend)) ||
          new Set(backends).size !== backends.length) {
        errors.push(`${where}: backends must be a non-empty list of distinct backends (${BACKENDS.join(', ')})`);
      }
    });
  }
  if (!text(entry.engineVersion)) errors.push(`${at}.engineVersion must be a non-empty string`);
  if (Object.hasOwn(entry, 'bindingVersion') && !text(entry.bindingVersion)) {
    errors.push(`${at}.bindingVersion must be a non-empty string when present`);
  }
  if (!isDay(entry.measuredFrom)) errors.push(`${at}.measuredFrom must be a calendar date YYYY-MM-DD`);
  if (Object.hasOwn(entry, 'carryover')) {
    const carryover = entry.carryover;
    if (!isObject(carryover)) {
      errors.push(`${at}.carryover must be an object`);
      return;
    }
    unknownFields(carryover, CARRYOVER_FIELDS, `${at}.carryover`, errors);
    if (typeof carryover.measured !== 'string' || !MONTH.test(carryover.measured)) {
      errors.push(`${at}.carryover.measured must be YYYY-MM`);
    }
    if (!text(carryover.reason)) errors.push(`${at}.carryover.reason must be a non-empty string`);
    keyListErrors(carryover.keys, `${at}.carryover.keys`, engine, errors);
  }
}

/** Errors in a parsed board configuration, as "path: message" strings. */
export function boardErrors(board, reviewedIds) {
  if (!isObject(board)) return [`${BOARD_FILE}: not a JSON object`];
  const errors = [];
  unknownFields(board, FIELDS, BOARD_FILE, errors);
  if (board.schema !== BOARD_SCHEMA) errors.push(`${BOARD_FILE}: schema must be ${BOARD_SCHEMA}`);
  if (board.schemaVersion !== BOARD_VERSION) errors.push(`${BOARD_FILE}: schemaVersion must be ${BOARD_VERSION}`);
  if (!text(board.description)) errors.push(`${BOARD_FILE}: description must be a non-empty string`);
  if (!isObject(board.engines) || !Object.keys(board.engines).length) {
    errors.push(`${BOARD_FILE}: engines must be a non-empty object (one measurement set per engine)`);
  } else {
    const names = Object.keys(board.engines);
    names.forEach((name, index) => {
      if (index > 0 && codepoint(names[index - 1], name) >= 0) errors.push(`${BOARD_FILE}: engines must be sorted by name`);
      engineErrors(name, board.engines[name], reviewedIds, errors);
    });
  }
  if (!Array.isArray(board.retiredKeys)) {
    errors.push(`${BOARD_FILE}: retiredKeys must be an array`);
    return errors;
  }
  board.retiredKeys.forEach((item, index) => {
    const at = `${BOARD_FILE}: retiredKeys[${index}]`;
    if (!isObject(item)) {
      errors.push(`${at} must be an object`);
      return;
    }
    unknownFields(item, RETIRED_FIELDS, at, errors);
    if (!keyParts(item.key)) errors.push(`${at}: ${JSON.stringify(item.key)} is not a measured row key`);
    if (!text(item.reason)) errors.push(`${at}: reason must be a non-empty string`);
    if (!isDay(item.retiredAt)) errors.push(`${at}: retiredAt must be a calendar date YYYY-MM-DD`);
    if (index > 0 && codepoint(String(board.retiredKeys[index - 1]?.key), String(item.key)) >= 0) {
      errors.push(`${BOARD_FILE}: retiredKeys must be unique and sorted by key`);
    }
    const carried = Object.values(isObject(board.engines) ? board.engines : {})
      .some((entry) => Array.isArray(entry?.carryover?.keys) && entry.carryover.keys.includes(item.key));
    if (carried) errors.push(`${at}: ${item.key} is both retired and carried over`);
  });
  return errors;
}

/** The normalized, validated board: { engines, retired: Map(key -> {reason, retiredAt}) }. */
export function normalizeBoard(board) {
  const engines = {};
  for (const [name, entry] of Object.entries(board.engines)) {
    engines[name] = entry.set === PRE_VERSION
      ? { set: PRE_VERSION, measured: entry.measured }
      : {
        set: SESSIONS, configurations: entry.configurations.map((item) => ({ id: item.id, backends: [...item.backends] })),
        engineVersion: entry.engineVersion,
        bindingVersion: entry.bindingVersion ?? null, measuredFrom: entry.measuredFrom,
        carryover: entry.carryover ? { ...entry.carryover, keys: [...entry.carryover.keys] } : null,
      };
  }
  return {
    engines,
    retired: new Map(board.retiredKeys.map((item) => [item.key, { reason: item.reason, retiredAt: item.retiredAt }])),
  };
}

export const boardExists = (root) => existsSync(join(root, BOARD_FILE));

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
  return normalizeBoard(board);
}

/**
 * Why a published session is not in its engine's displayed set, or null when it is.
 * `kind` is "configuration" when its configuration is not one the set lists (or it names
 * none), else "set" (another engine version, binding version or an earlier measurement).
 */
export function setMismatch(board, session) {
  const row = session.row ?? {};
  const set = board.engines[row.engine];
  if (!set) return { kind: 'set', reason: `configs/board.json declares no measurement set for ${row.engine}` };
  if (set.set !== SESSIONS) {
    return { kind: 'set', reason: `${row.engine}'s displayed set is its rows from before version recording (${set.measured})` };
  }
  const configuration = set.configurations.find((item) => item.id === row.configurationId);
  if (!configuration) {
    return {
      kind: 'configuration',
      reason: row.configurationId
        ? `configuration ${row.configurationId} is not in ${row.engine}'s set in configs/board.json`
        : 'the session names no reviewed configuration file (configurationSource)',
    };
  }
  if (!configuration.backends.includes(row.backend)) {
    return {
      kind: 'configuration',
      reason: `backend ${row.backend} is not displayed for configuration ${row.configurationId} ` +
        `(${configuration.backends.join(', ')} only in configs/board.json)`,
    };
  }
  const engineVersion = row.build?.engineVersion ?? null;
  const bindingVersion = row.build?.bindingVersion ?? null;
  if (engineVersion !== set.engineVersion) {
    return { kind: 'set', reason: `engine version ${engineVersion ?? 'not recorded'} is not ${row.engine}'s set (${set.engineVersion})` };
  }
  if (bindingVersion !== set.bindingVersion) {
    return { kind: 'set', reason: `binding version ${bindingVersion ?? 'none'} is not ${row.engine}'s set (${set.bindingVersion ?? 'none'})` };
  }
  const time = Date.parse(String(session.timestampIso));
  if (!Number.isFinite(time) || time < Date.parse(`${set.measuredFrom}T00:00:00Z`)) {
    return { kind: 'set', reason: `measured before ${row.engine}'s set (from ${set.measuredFrom})` };
  }
  return null;
}

/** True when the session belongs to its engine's displayed set. */
export const inSet = (board, session) => setMismatch(board, session) === null;
