/**
 * Published-session history (data/sessions/index.json): every published session per row key,
 * the session data/benchmarks.json displays for each key, and the rows that left the board.
 *
 * Selection rule (SELECTION_RULE): a key's displayed session is chosen only from its published
 * sessions in the engine's displayed measurement set (configs/board.json, scripts/lib/board.mjs):
 * the newest verified, else the newest unverified. A failed session is never displayed. A retired
 * key displays nothing. "Newest" is the latest timestampIso, ties broken by sessionId (plain
 * code-point order), so the result never depends on input order. Publishing never deletes a
 * session, and a row that leaves the board (replaced or retired) is kept in replacedRows.
 *
 * This module holds the structural checks, which need no board; scripts/lib/sets.mjs applies
 * the board. Everything here is pure (no I/O).
 */
import {
  assertSchema, evidenceUrl, jsonEqual, PublicationError, rowInvariantErrors, rowKey,
} from './publication.mjs';
import { inSet } from './board.mjs';

export const INDEX_SCHEMA = 'xebench-sessions-index';
export const INDEX_VERSION = '1.1';
export const POLICY = 'publication-policy-v1';
export const SELECTION_RULE = 'engine-set-newest-verified-else-newest-unverified';
const PREFERENCE = ['verified', 'unverified'];
/** Rules of a key that displays a session, and of one that displays none. */
export const DISPLAY_RULES = Object.freeze(['newest-verified', 'newest-unverified']);
export const NO_SESSION = 'no-session-in-set';
export const RETIRED = 'retired';

const codepoint = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
export const isLegacy = (row) => row.provenance === 'measured' && (row.validation === 'legacy' || !Object.hasOwn(row, 'validation'));

const instant = (value) => {
  const time = Date.parse(String(value));
  return Number.isFinite(time) ? time : null;
};

/**
 * Total order of a key's sessions, oldest first: the instant timestampIso names (so
 * "…Z" and "…+00:00" or different fraction digits compare correctly), then the
 * timestampIso text, then sessionId.
 */
export const compareSessions = (a, b) => {
  const [x, y] = [instant(a.timestampIso), instant(b.timestampIso)];
  return (x !== null && y !== null ? Math.sign(x - y) : 0) || codepoint(String(a.timestampIso), String(b.timestampIso)) ||
    codepoint(String(a.sessionId), String(b.sessionId));
};

/**
 * The displayed session of a key, by SELECTION_RULE: { sessionId, rule }; sessionId is null
 * when the key is retired or has no displayable session in its engine's set. Without a
 * board, every session is a candidate (used only to test the ordering).
 */
export function selectSession(sessions, board = null, key = null) {
  if (board && key !== null && board.retired.has(key)) return { sessionId: null, rule: RETIRED };
  const pool = board ? sessions.filter((session) => inSet(board, session)) : sessions;
  for (const validation of PREFERENCE) {
    const candidates = pool.filter((session) => session.validation === validation).sort(compareSessions);
    if (candidates.length) return { sessionId: candidates[candidates.length - 1].sessionId, rule: `newest-${validation}` };
  }
  return { sessionId: null, rule: NO_SESSION };
}

export const emptyIndex = () => ({
  schema: INDEX_SCHEMA, schemaVersion: INDEX_VERSION, policy: POLICY, selectionRule: SELECTION_RULE, keys: [],
});

/** One index entry for a publishable candidate (the session plus the row it publishes). */
export function sessionEntry({ sessionId, timestampIso, bundleSha256, row }) {
  return {
    sessionId, timestampIso, bundleSha256, url: evidenceUrl(bundleSha256), validation: row.validation,
    guardsPassed: row.guardsPassed, row,
  };
}

/**
 * Every structural invariant of the index on its own and against the benchmarks envelope it
 * describes, as "path: message" errors. Board-independent: whether the selection matches
 * configs/board.json is checked by scripts/lib/sets.mjs (boardDisplayErrors). Fail closed:
 * callers refuse on any error.
 */
export function indexErrors(index, envelope, schemas) {
  const errors = [];
  const add = (message) => errors.push(message);
  try {
    assertSchema(index, schemas.index, 'data/sessions/index.json');
  } catch (error) {
    if (!(error instanceof PublicationError)) throw error;
    return [error.message];
  }
  const rows = new Map(envelope.rows.map((row) => [rowKey(row), row]));
  const seenSessions = new Map();
  index.keys.forEach((entry, position) => {
    const at = `data/sessions/index.json keys[${position}] (${entry.key})`;
    if (position > 0 && codepoint(index.keys[position - 1].key, entry.key) >= 0) add(`${at}: keys must be unique and sorted`);
    if (!entry.sessions.length && !entry.replacedRows.length) add(`${at}: an entry holds at least one session or row`);
    entry.sessions.forEach((session, index_) => {
      const where = `${at} sessions[${index_}]`;
      if (index_ > 0 && compareSessions(entry.sessions[index_ - 1], session) >= 0) {
        add(`${where}: sessions must be unique and sorted oldest first (timestampIso, sessionId)`);
      }
      if (instant(session.timestampIso) === null) add(`${where}: timestampIso is not a date and time`);
      if (seenSessions.has(session.sessionId)) add(`${where}: session ${session.sessionId} is also under ${seenSessions.get(session.sessionId)}`);
      seenSessions.set(session.sessionId, entry.key);
      const { row } = session;
      try {
        assertSchema(row, schemas.row, `${where}.row`, schemas.benchmarks);
      } catch (error) {
        if (!(error instanceof PublicationError)) throw error;
        add(error.message);
        return;
      }
      rowInvariantErrors(row, `${where}.row`).forEach(add);
      if (rowKey(row) !== entry.key) add(`${where}: row key ${rowKey(row)} differs from the entry key`);
      if (row.provenance !== 'measured') add(`${where}: only measured rows are published from sessions`);
      if (row.validation !== session.validation || row.guardsPassed !== session.guardsPassed) {
        add(`${where}: validation/guardsPassed differ from the row's`);
      }
      const evidence = { sessionIds: [session.sessionId], bundleSha256: session.bundleSha256, url: session.url };
      if (!jsonEqual(row.evidence, evidence)) add(`${where}: row evidence does not name this session and bundle`);
      let url;
      try {
        url = evidenceUrl(session.bundleSha256);
      } catch {
        url = undefined;
      }
      if (session.url !== url) add(`${where}: url is not the hosted bundle URL`);
      if (row.sourceDate !== String(session.timestampIso).slice(0, 7)) add(`${where}: row sourceDate disagrees with timestampIso`);
    });
    const ids = new Set(entry.sessions.map((session) => session.sessionId));
    const shown = rows.get(entry.key);
    if (entry.displayed === null) {
      if (DISPLAY_RULES.includes(entry.rule)) add(`${at}: rule ${entry.rule} needs a displayed session`);
      if (shown && Object.hasOwn(shown, 'evidence')) add(`${at}: displays no session, but data/benchmarks.json shows a session row`);
    } else {
      const displayed = entry.sessions.find((session) => session.sessionId === entry.displayed);
      if (!displayed) {
        add(`${at}: displayed ${entry.displayed} is not a session of this key`);
      } else {
        if (entry.rule !== `newest-${displayed.validation}`) add(`${at}: rule ${entry.rule} does not fit a ${displayed.validation} session`);
        if (!shown || !jsonEqual(shown, displayed.row)) add(`${at}: data/benchmarks.json does not show the displayed session's row`);
      }
    }
    entry.replacedRows.forEach((item, index_) => {
      const where = `${at} replacedRows[${index_}]`;
      if (!isObject(item.row) || rowKey(item.row) !== entry.key) {
        add(`${where}: row is not this key`);
        return;
      }
      if (Object.hasOwn(item.row, 'evidence')) add(`${where}: a session row stays in sessions, not in replacedRows`);
      if (item.reason === RETIRED) {
        if (!isLegacy(item.row) && item.row.validation !== 'failed') add(`${where}: a retired entry keeps a legacy or failed row`);
        return;
      }
      if (!ids.has(item.replacedBy)) add(`${where}: replacedBy is not a session of this key`);
      const kind = isLegacy(item.row) ? 'replace-legacy' : item.row.validation === 'failed' ? 'replace-failed' : null;
      if (item.reason !== kind) {
        add(`${where}: a ${item.reason} entry must keep a ${item.reason === 'replace-legacy' ? 'legacy' : 'failed'} row`);
      }
    });
  });
  const indexed = new Map(index.keys.map((entry) => [entry.key, entry]));
  for (const row of envelope.rows) {
    const key = rowKey(row);
    if (indexed.get(key)?.displayed) continue;
    if (row.validation === 'verified') {
      add(`refusing to emit validation=verified for row ${key}: no published session backs it in data/sessions/index.json`);
    } else if (Object.hasOwn(row, 'evidence')) {
      add(`row ${key} carries evidence but no displayed session in data/sessions/index.json`);
    }
  }
  return errors;
}

export function assertIndex(index, envelope, schemas, label = 'data/sessions/index.json') {
  const errors = indexErrors(index, envelope, schemas);
  if (errors.length) throw new PublicationError(`${label} is inconsistent:\n  ${errors.slice(0, 20).join('\n  ')}`);
}
