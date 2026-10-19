/**
 * Published-session history (data/sessions/index.json) and the explicit selection of
 * the row data/benchmarks.json displays for each key.
 *
 * Selection rule (SELECTION_RULE): among a key's published sessions of a configuration
 * the board displays (configs/board.json, scripts/lib/board.mjs), the newest verified, else
 * the newest unverified, else the newest failed. "Newest" is the latest timestampIso, ties
 * broken by sessionId (plain code-point order), so the result never depends on input order.
 * A published session of another configuration stays in the history and is never displayed. Publishing never deletes a session: every published session
 * stays in the index, and a replaced legacy or failed row is kept in replacedRows.
 *
 * Everything here is pure (no I/O) so that selection and history are testable alone.
 */
import {
  assertSchema, compareRows, evidenceUrl, jsonEqual, PublicationError, rowInvariantErrors, rowKey,
} from './publication.mjs';

export const INDEX_SCHEMA = 'xebench-sessions-index';
export const INDEX_VERSION = '1.0';
export const POLICY = 'publication-policy-v1';
export const SELECTION_RULE = 'newest-verified-else-newest-unverified-else-newest-failed';
const PREFERENCE = ['verified', 'unverified', 'failed'];

const codepoint = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const clone = (value) => JSON.parse(JSON.stringify(value));
const isLegacy = (row) => row.provenance === 'measured' && (row.validation === 'legacy' || !Object.hasOwn(row, 'validation'));

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
 * The displayed session of a key, by SELECTION_RULE: { sessionId, rule }. With a board,
 * only sessions whose row names a configuration it lists are candidates.
 */
export function selectSession(sessions, board = null) {
  const shown = board ? sessions.filter((session) => board.displayConfigurations.includes(session.row?.configurationId))
    : sessions;
  if (board && !shown.length) {
    throw new PublicationError('no published session of a configuration configs/board.json lists; nothing to display');
  }
  for (const validation of PREFERENCE) {
    const pool = shown.filter((session) => session.validation === validation).sort(compareSessions);
    if (pool.length) return { sessionId: pool[pool.length - 1].sessionId, rule: `newest-${validation}` };
  }
  throw new PublicationError('a key needs at least one verified, unverified or failed session');
}

/**
 * `board` is the board configuration, or a function that loads it. It is resolved only when
 * a key is selected, so files without published sessions need no configs/board.json.
 */
const boardResolver = (board) => {
  let resolved = null;
  return () => (resolved ??= typeof board === 'function' ? board() : board);
};

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
 * Every invariant of the index on its own and against the benchmarks envelope it
 * describes, as "path: message" errors. Fail closed: callers refuse on any error.
 */
export function indexErrors(index, envelope, schemas, board) {
  const errors = [];
  const getBoard = boardResolver(board);
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
    let selected;
    try {
      selected = selectSession(entry.sessions, getBoard());
    } catch (error) {
      if (!(error instanceof PublicationError)) throw error;
      add(`${at}: ${error.message}`);
      return;
    }
    if (entry.displayed !== selected.sessionId || entry.rule !== selected.rule) {
      add(`${at}: displays ${entry.displayed} (${entry.rule}) but ${SELECTION_RULE} gives ${selected.sessionId} ` +
        `(${selected.rule})`);
    }
    entry.replacedRows.forEach((item, index_) => {
      if (!ids.has(item.replacedBy)) add(`${at} replacedRows[${index_}]: replacedBy is not a session of this key`);
      if (!isObject(item.row) || rowKey(item.row) !== entry.key) {
        add(`${at} replacedRows[${index_}]: row is not this key`);
        return;
      }
      const kind = isLegacy(item.row) ? 'replace-legacy' : item.row.validation === 'failed' ? 'replace-failed' : null;
      if (item.reason !== kind) {
        add(`${at} replacedRows[${index_}]: a ${item.reason} entry must keep a ${item.reason === 'replace-legacy' ? 'legacy' : 'failed'} row`);
      }
    });
    const shown = rows.get(entry.key);
    const expected = entry.sessions.find((session) => session.sessionId === entry.displayed)?.row;
    if (!shown || !jsonEqual(shown, expected)) {
      add(`${at}: data/benchmarks.json does not show the displayed session's row`);
    }
  });
  const indexed = new Set(index.keys.map((entry) => entry.key));
  for (const row of envelope.rows) {
    const key = rowKey(row);
    if (indexed.has(key)) continue;
    if (row.validation === 'verified') {
      add(`refusing to emit validation=verified for row ${key}: no published session backs it in data/sessions/index.json`);
    } else if (Object.hasOwn(row, 'evidence')) {
      add(`row ${key} carries evidence but no published session in data/sessions/index.json`);
    }
  }
  return errors;
}

export function assertIndex(index, envelope, schemas, board, label = 'data/sessions/index.json') {
  if (!board) throw new PublicationError(`${label}: checking the displayed rows needs configs/board.json`);
  const errors = indexErrors(index, envelope, schemas, board);
  if (errors.length) throw new PublicationError(`${label} is inconsistent:\n  ${errors.slice(0, 20).join('\n  ')}`);
}

/**
 * Add candidate sessions to the history and re-select each touched key. Pure: returns
 * the new rows and index plus one action per touched key; nothing is decided by input
 * order. `needs` lists the flags the plan requires:
 *   --replace-legacy  a key's current row is a legacy row (kept in replacedRows);
 *   --replace-failed  a key's current row is failed (an unindexed failed row is kept in
 *                     replacedRows; an indexed failed session stays in the history).
 * A session already in the index with identical content is a no-op; the same session
 * with different content, or under another key, is refused. Only sessions of a
 * configuration the board lists are displayed; a key needs at least one.
 */
export function planPublication(envelope, index, candidates, board) {
  if (!board) throw new PublicationError('planning a publication needs configs/board.json');
  const getBoard = boardResolver(board);
  const entries = new Map(index.keys.map((entry) => [entry.key, clone(entry)]));
  const owner = new Map(index.keys.flatMap((entry) => entry.sessions.map((session) => [session.sessionId, entry.key])));
  const rows = new Map(envelope.rows.map((row) => [rowKey(row), row]));
  const touched = new Map();
  const ordered = [...candidates].sort((a, b) => codepoint(rowKey(a.row), rowKey(b.row)) || compareSessions(a, b));
  for (const candidate of ordered) {
    const key = rowKey(candidate.row);
    const session = sessionEntry(candidate);
    if (owner.has(session.sessionId)) {
      const existing = entries.get(owner.get(session.sessionId)).sessions.find((item) => item.sessionId === session.sessionId);
      if (owner.get(session.sessionId) === key && jsonEqual(existing, session)) {
        if (!touched.has(key)) touched.set(key, { unchanged: [] });
        (touched.get(key).unchanged ??= []).push(session.sessionId);
        continue;
      }
      throw new PublicationError(
        `session ${session.sessionId} is already published${owner.get(session.sessionId) === key ? ' with different content' : ` under ${owner.get(session.sessionId)}`}; ` +
          'a published session is never rewritten',
      );
    }
    owner.set(session.sessionId, key);
    const entry = entries.get(key) ?? { key, displayed: null, rule: null, sessions: [], replacedRows: [] };
    entry.sessions = [...entry.sessions, session].sort(compareSessions);
    entries.set(key, entry);
    if (!touched.has(key)) touched.set(key, {});
    (touched.get(key).added ??= []).push(session.sessionId);
  }

  const actions = [];
  const needs = new Set();
  for (const [key, change] of [...touched].sort(([a], [b]) => codepoint(a, b))) {
    const entry = entries.get(key);
    const before = rows.get(key);
    const wasIndexed = index.keys.some((item) => item.key === key);
    let selected;
    try {
      selected = selectSession(entry.sessions, getBoard());
    } catch (error) {
      if (!(error instanceof PublicationError)) throw error;
      throw new PublicationError(`row ${key}: ${error.message}`);
    }
    const action = { key, added: change.added ?? [], unchanged: change.unchanged ?? [], displayed: selected.sessionId, rule: selected.rule, needs: [] };
    if (!change.added) {
      actions.push({ ...action, change: 'already published' });
      continue;
    }
    if (!wasIndexed && before) {
      const reason = isLegacy(before) ? 'replace-legacy' : before.validation === 'failed' ? 'replace-failed' : null;
      if (!reason) {
        throw new PublicationError(`row ${key} is ${before.validation ?? before.provenance} but no published session backs it`);
      }
      entry.replacedRows = [...entry.replacedRows, { reason, replacedBy: selected.sessionId, row: before }];
      action.needs.push(`--${reason}`);
      action.change = `replaces the ${isLegacy(before) ? 'legacy' : 'failed'} row`;
    } else if (!before) {
      action.change = 'new row';
    } else if (entry.displayed === selected.sessionId) {
      action.change = 'history only (displayed row unchanged)';
    } else {
      if (before.validation === 'failed') action.needs.push('--replace-failed');
      action.change = `displays ${selected.sessionId} instead of ${entry.displayed}`;
    }
    entry.displayed = selected.sessionId;
    entry.rule = selected.rule;
    rows.set(key, entry.sessions.find((session) => session.sessionId === selected.sessionId).row);
    action.needs.forEach((flag) => needs.add(flag));
    actions.push(action);
  }
  return {
    rows: [...rows.values()].sort(compareRows),
    index: { ...emptyIndex(), keys: [...entries.values()].sort((a, b) => codepoint(a.key, b.key)) },
    actions,
    needs: [...needs].sort(),
  };
}
