/**
 * Measurement sets: what the board displays, given the history and configs/board.json
 * (docs/RUN-CONDITIONS.md, Measurement sets).
 *
 * data/benchmarks.json `rows` holds only displayed rows: vendor and community rows, and each
 * engine's rows from its one displayed set. Every other measured row lives in
 * data/sessions/index.json: superseded and staged sessions, rows replaced by a session, and
 * retired rows. For each measured row key:
 *
 *   retired (configs/board.json retiredKeys)  nothing is displayed; a row without a session
 *                                             moves to replacedRows (reason "retired").
 *   a verified or unverified session in the engine's set
 *                                             the newest is displayed (SELECTION_RULE); a row
 *                                             without a session it replaces moves to replacedRows.
 *   otherwise, a row without versions        displayed only when the engine's set is its
 *                                             pre-version-recording rows of that month, or the
 *                                             key is carried over; a failed row never is.
 *   otherwise                                nothing is displayed.
 *
 * Completeness: a key the board displays now leaves it only when it is retired. So a new set is
 * displayed only once every displayed key of the engine has a session in it or is retired; until
 * then the update is refused, and new sessions stay in the history (aggregate.mjs --stage).
 *
 * Everything here is pure (no I/O).
 */
import { compareRows, jsonEqual, PublicationError, rowKey } from './publication.mjs';
import { PRE_VERSION, SESSIONS } from './board.mjs';
import { legacyDateMismatch } from './legacy-dates.mjs';
import {
  compareSessions, emptyIndex, isLegacy, RETIRED, selectSession, sessionEntry,
} from './history.mjs';

const codepoint = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const clone = (value) => JSON.parse(JSON.stringify(value));

/** One line naming an engine's displayed set. */
export function describeSet(engine, set) {
  if (!set) return `${engine}: no set declared`;
  if (set.set === PRE_VERSION) return `${engine}: rows without versions, measured ${set.measured}`;
  return `${engine}: ${set.engineVersion}${set.bindingVersion ? ` + ${set.bindingVersion}` : ''}, ` +
    `${set.configurations.map((item) => `${item.id} (${item.backends.join('/')})`).join(', ')}, measured from ${set.measuredFrom}`;
}

/** Whether an engine's set displays this row without versions (and why not). */
function versionlessDisplay(row, key, set, engine) {
  if (row.validation === 'failed') {
    return { shown: false, error: `row ${key} is failed and is never displayed; list it in configs/board.json retiredKeys` };
  }
  if (!isLegacy(row)) {
    return { shown: false, error: `row ${key} is ${row.validation} but no published session backs it` };
  }
  const frozen = legacyDateMismatch(row);
  if (frozen) return { shown: false, error: `row ${key} has no version and ${frozen}` };
  if (!set) return { shown: false, error: `row ${key}: configs/board.json declares no measurement set for ${engine}` };
  const month = row.sourceDate ?? 'an unknown month';
  if (set.set === PRE_VERSION) {
    if (row.sourceDate === set.measured) return { shown: true };
    return { shown: false, error: `row ${key} (measured ${month}) is not in ${describeSet(engine, set)}; list it in retiredKeys` };
  }
  const carried = set.carryover?.keys.includes(key);
  if (carried && row.sourceDate === set.carryover.measured) return { shown: true };
  return {
    shown: false,
    error: `row ${key} has no version (measured ${month}) and is not in ${describeSet(engine, set)}: ` +
      'publish a session of the set for it, or list the key in configs/board.json retiredKeys',
  };
}

/**
 * Apply the board to the history and the current rows. Returns { rows, index, actions, needs,
 * errors }: the rows data/benchmarks.json displays, the index, one action per key whose display
 * or history changes, the flags those changes need (--replace-legacy, --replace-failed) and every
 * reason the result is refused. Never throws for a policy problem; callers refuse on errors.
 * `added` maps a key to the session ids this run adds (for reporting).
 */
export function applyBoard(envelope, index, board, { added = new Map() } = {}) {
  const entries = new Map(index.keys.map((entry) => [entry.key, clone(entry)]));
  const current = new Map(envelope.rows.map((row) => [rowKey(row), row]));
  const out = new Map();
  const errors = [];
  const actions = [];
  const needs = new Set();
  for (const [key, row] of current) if (row.provenance !== 'measured') out.set(key, row);
  const keys = new Set([...entries.keys(), ...[...current].filter(([, row]) => row.provenance === 'measured').map(([key]) => key)]);
  for (const key of [...keys].sort(codepoint)) {
    const engine = key.split('|')[0];
    const set = board.engines[engine];
    let entry = entries.get(key) ?? null;
    const before = current.get(key) ?? null;
    const unindexed = before && !Object.hasOwn(before, 'evidence') ? before : null;
    const selected = selectSession(entry?.sessions ?? [], board, key);
    const action = { key, added: added.get(key) ?? [], displayed: selected.sessionId, rule: selected.rule, needs: [], change: null };
    const keep = (item) => {
      entry ??= { key, displayed: null, rule: selected.rule, sessions: [], replacedRows: [] };
      entry.replacedRows = [...entry.replacedRows, item];
      entries.set(key, entry);
    };
    if (selected.rule === RETIRED) {
      const retired = board.retired.get(key);
      if (unindexed) {
        keep({ reason: RETIRED, note: retired.reason, retiredAt: retired.retiredAt, row: unindexed });
        action.change = 'retired: the row moves to the history';
      } else if (before) {
        action.change = 'retired: the row leaves the board';
      }
    } else if (selected.sessionId) {
      const row = entry.sessions.find((session) => session.sessionId === selected.sessionId).row;
      out.set(key, row);
      if (unindexed) {
        const reason = isLegacy(unindexed) ? 'replace-legacy' : unindexed.validation === 'failed' ? 'replace-failed' : null;
        if (!reason) {
          errors.push(`row ${key} is ${unindexed.validation ?? unindexed.provenance} but no published session backs it`);
        } else {
          keep({ reason, replacedBy: selected.sessionId, row: unindexed });
          action.needs.push(`--${reason}`);
          action.change = `replaces the ${reason === 'replace-legacy' ? 'legacy' : 'failed'} row`;
        }
      } else if (!before) {
        action.change = 'new row';
      } else if (entry.displayed !== selected.sessionId) {
        if (before.validation === 'failed') action.needs.push('--replace-failed');
        action.change = `displays ${selected.sessionId} instead of ${entry.displayed}`;
      } else if (action.added.length) {
        action.change = 'history only (displayed row unchanged)';
      }
    } else if (unindexed) {
      const display = versionlessDisplay(unindexed, key, set, engine);
      if (display.shown) out.set(key, unindexed);
      else errors.push(display.error);
      if (action.added.length) action.change = 'history only (no session of the set; the row without versions stays)';
    } else if (before) {
      errors.push(`row ${key} is displayed but has no verified or unverified session in ${describeSet(engine, set)}: ` +
        'publish one, or list the key in configs/board.json retiredKeys');
    } else if (action.added.length) {
      action.change = 'history only (not displayed: no session in the set)';
    }
    if (entry) {
      entry.displayed = selected.sessionId;
      entry.rule = selected.rule;
    }
    if (out.get(key)?.validation === 'failed') errors.push(`row ${key}: a failed row is never displayed`);
    action.needs.forEach((flag) => needs.add(flag));
    if (action.change) actions.push(action);
  }
  for (const [key, row] of out) {
    if (row.provenance === 'measured' && !board.engines[row.engine]) {
      errors.push(`row ${key}: configs/board.json declares no measurement set for ${row.engine}`);
    }
  }
  return {
    rows: [...out.values()].sort(compareRows),
    index: { ...emptyIndex(), keys: [...entries.values()].sort((a, b) => codepoint(a.key, b.key)) },
    actions,
    needs: [...needs].sort(),
    errors: [...new Set(errors)],
  };
}

/**
 * Add candidate sessions to the history, then apply the board. Pure; nothing depends on
 * input order. A session already in the index with identical content is a no-op; the same
 * session with different content, or under another key, is refused.
 */
export function planPublication(envelope, index, candidates, board) {
  if (!board) throw new PublicationError('planning a publication needs configs/board.json');
  const entries = new Map(index.keys.map((entry) => [entry.key, clone(entry)]));
  const owner = new Map(index.keys.flatMap((entry) => entry.sessions.map((session) => [session.sessionId, entry.key])));
  const added = new Map();
  const unchanged = new Map();
  const ordered = [...candidates].sort((a, b) => codepoint(rowKey(a.row), rowKey(b.row)) || compareSessions(a, b));
  for (const candidate of ordered) {
    const key = rowKey(candidate.row);
    const session = sessionEntry(candidate);
    if (owner.has(session.sessionId)) {
      const existing = entries.get(owner.get(session.sessionId)).sessions.find((item) => item.sessionId === session.sessionId);
      if (owner.get(session.sessionId) === key && jsonEqual(existing, session)) {
        unchanged.set(key, [...(unchanged.get(key) ?? []), session.sessionId]);
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
    added.set(key, [...(added.get(key) ?? []), session.sessionId]);
  }
  const plan = applyBoard(envelope, { ...index, keys: [...entries.values()] }, board, { added });
  for (const [key, ids] of unchanged) {
    if (!added.has(key) && !plan.actions.some((action) => action.key === key)) {
      const entry = plan.index.keys.find((item) => item.key === key);
      plan.actions.push({ key, added: [], unchanged: ids, displayed: entry.displayed, rule: entry.rule, needs: [], change: 'already published' });
    }
  }
  plan.actions.sort((a, b) => codepoint(a.key, b.key));
  plan.added = [...added.values()].flat().length;
  return plan;
}

/**
 * Errors when the committed files are not what the board gives: applying configs/board.json to
 * them must change nothing and refuse nothing.
 */
export function boardDisplayErrors(envelope, index, board) {
  const plan = applyBoard(envelope, index, board);
  const errors = [...plan.errors];
  const shown = (rows) => rows.map((row) => rowKey(row));
  if (!jsonEqual(plan.rows, [...envelope.rows].sort(compareRows))) {
    const want = new Set(shown(plan.rows));
    const have = new Set(shown(envelope.rows));
    const extra = [...have].filter((key) => !want.has(key));
    const missing = [...want].filter((key) => !have.has(key));
    errors.push('data/benchmarks.json does not show what configs/board.json selects' +
      (extra.length ? `; not displayed by the board: ${extra.join(', ')}` : '') +
      (missing.length ? `; displayed by the board but missing: ${missing.join(', ')}` : '') +
      '. Run node scripts/aggregate.mjs --apply-board --update');
  }
  const header = ({ keys, ...rest }) => rest;
  if (!jsonEqual(header(plan.index), header(index))) {
    errors.push('data/sessions/index.json header is not the current selection rule; run node scripts/aggregate.mjs --apply-board --update');
  }
  plan.index.keys.forEach((entry) => {
    const stored = index.keys.find((item) => item.key === entry.key);
    if (!stored) {
      errors.push(`data/sessions/index.json has no entry for ${entry.key}; run node scripts/aggregate.mjs --apply-board --update`);
    } else if (stored.displayed !== entry.displayed || stored.rule !== entry.rule) {
      errors.push(`data/sessions/index.json ${entry.key}: displays ${stored.displayed} (${stored.rule}) but configs/board.json ` +
        `gives ${entry.displayed} (${entry.rule})`);
    } else if (!jsonEqual(stored, entry)) {
      errors.push(`data/sessions/index.json ${entry.key}: history differs from what configs/board.json gives`);
    }
  });
  return errors;
}
