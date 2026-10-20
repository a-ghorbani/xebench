/**
 * Publication from sanitized evidence bundles (scripts/export_evidence.py output).
 *
 * A bundle is trusted only through this chain:
 *   1. scripts/publication_policy.py verifies it (export_evidence.verify: hashes,
 *      allowlist, privacy screen, summaries), re-checks every session's evidence with
 *      raw_session's cross-field rules, and evaluates publication-policy-v1 from facts
 *      it recomputes (never from recorded verdicts).
 *   2. readBoundBundle re-reads manifest.json and every file this process uses, and
 *      checks their bytes against the verified bundleSha256 and the manifest checksums.
 *   3. recomputeSummary re-derives n, median, IQR, min and max from the repetitions; a
 *      record summary that disagrees is refused, and rows use only the recomputed values.
 */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  assertFinite, buildFromSession, CONFIGURATION_ID, evidenceUrl, friendlyDevice, jsonEqual, PublicationError, quantBitsFor,
} from './publication.mjs';

export const POLICY_SCRIPT = fileURLToPath(new URL('../publication_policy.py', import.meta.url));
export const POLICY_VERSION = 'publication-policy-v1';
export const METRICS = Object.freeze(['loadMs', 'ttftMs', 'prefillTps', 'decodeTps']);
// raw_session.TOLERANCE: summaries are compared with the validator's tolerance.
const TOLERANCE = 1e-12;
const HEX64 = /^[0-9a-f]{64}$/;
const SOURCE_NAME = 'xebench';

/**
 * Marketing chip names by device model (configs/devices.json chipName): the row's chipName.
 * An unlisted model, or one without chipName, gives none. A missing table gives an empty map.
 */
export function loadChipNames(root) {
  try {
    const table = JSON.parse(readFileSync(join(root, 'configs/devices.json'), 'utf8'));
    return new Map((table.devices ?? []).filter((entry) => typeof entry.chipName === 'string' && entry.chipName)
      .map((entry) => [entry.model, entry.chipName]));
  } catch (error) {
    if (error.code === 'ENOENT') return new Map();
    throw new PublicationError(`configs/devices.json: unreadable (${error.message})`);
  }
}
const SOURCE_URL = 'https://github.com/a-ghorbani/xebench/blob/main/METHODOLOGY.md';

export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const round1 = (n) => (n == null ? null : Math.round(n * 10) / 10);

/**
 * Bundle directories under `dir`: `dir` itself when it holds a manifest.json, else
 * every <64-hex> subdirectory (other files, e.g. evidence/README.md, are skipped).
 * Any other subdirectory or a symlink is refused.
 */
export function listBundles(dir) {
  let entries;
  try {
    if (lstatSync(join(dir, 'manifest.json'), { throwIfNoEntry: false })) return [dir];
    entries = readdirSync(dir, { withFileTypes: true });
  } catch (error) {
    throw new PublicationError(`cannot read bundle directory ${dir}: ${error.code ?? error.message}`);
  }
  const bundles = [];
  for (const entry of entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))) {
    if (entry.isSymbolicLink()) throw new PublicationError(`${join(dir, entry.name)}: symlinks are not bundles`);
    if (!entry.isDirectory()) continue;
    if (!HEX64.test(entry.name)) throw new PublicationError(`${join(dir, entry.name)}: not a <bundleSha256> directory`);
    bundles.push(join(dir, entry.name));
  }
  if (!bundles.length) throw new PublicationError(`${dir}: no evidence bundles`);
  return bundles;
}

/** Step 1: verify, re-check and evaluate one bundle in publication_policy.py. */
export function runPolicy(bundleDir, expectedSha) {
  const args = ['-I', POLICY_SCRIPT, bundleDir, ...(expectedSha ? ['--sha256', expectedSha] : [])];
  const result = spawnSync(process.env.XEBENCH_PYTHON || 'python3', args, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 });
  if (result.error) throw new PublicationError(`cannot run publication_policy.py: ${result.error.message}`);
  let report;
  try {
    report = JSON.parse(result.stdout);
  } catch {
    throw new PublicationError(`${bundleDir}: publication_policy.py failed (exit ${result.status}): ${result.stderr.trim()}`);
  }
  if (!isObject(report) || report.policy !== POLICY_VERSION || typeof report.valid !== 'boolean') {
    throw new PublicationError(`${bundleDir}: unexpected publication_policy.py output`);
  }
  if (report.valid !== (result.status === 0)) throw new PublicationError(`${bundleDir}: policy exit status disagrees with its report`);
  return report;
}

/**
 * Step 2: the manifest's bytes hash to `sha`, and each file read through `file()` has
 * the size and SHA-256 the manifest lists. Returns { manifest, file(path) }.
 */
export function readBoundBundle(bundleDir, sha) {
  if (!HEX64.test(String(sha))) throw new PublicationError(`${bundleDir}: no verified bundleSha256`);
  const read = (path) => {
    try {
      return readFileSync(join(bundleDir, path));
    } catch (error) {
      throw new PublicationError(`${bundleDir}/${path}: cannot read (${error.code ?? error.message})`);
    }
  };
  const manifestBytes = read('manifest.json');
  if (sha256(manifestBytes) !== sha) {
    throw new PublicationError(`${bundleDir}: manifest.json no longer hashes to the verified bundleSha256 ${sha}`);
  }
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  const listed = new Map((manifest.files ?? []).map((item) => [item.path, item]));
  const file = (path) => {
    const item = listed.get(path);
    if (!item) throw new PublicationError(`${bundleDir}: ${path} is not listed in the manifest`);
    const bytes = read(path);
    if (bytes.length !== item.bytes || sha256(bytes) !== item.sha256) {
      throw new PublicationError(`${bundleDir}/${path}: bytes differ from the verified manifest (tampered)`);
    }
    const value = JSON.parse(bytes.toString('utf8'));
    assertFinite(value, path);
    return value;
  };
  return { manifest, file };
}

/** raw_session.aggregate: linear interpolation at index (n - 1) * p. */
export function aggregate(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const percentile = (p) => {
    const index = (ordered.length - 1) * p;
    const lo = Math.floor(index);
    const hi = Math.ceil(index);
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo);
  };
  return {
    median: percentile(0.5), iqr: percentile(0.75) - percentile(0.25), min: ordered[0], max: ordered[ordered.length - 1],
    n: ordered.length,
  };
}

/** Step 3: the summary re-derived from the record's successful repetitions. */
export function recomputeSummary(record) {
  const complete = record.coldRuns.filter((attempt) => isObject(attempt) && attempt.status === 'complete');
  return Object.fromEntries(METRICS.map((metric) => {
    const values = complete.map((attempt) => attempt[metric]);
    if (values.some((value) => typeof value !== 'number' || !Number.isFinite(value))) {
      throw new PublicationError(`${record.runId}: a successful repetition lacks a finite ${metric}`);
    }
    return [metric, aggregate(values)];
  }));
}

const close = (a, b) => Math.abs(a - b) <= Math.max(TOLERANCE, TOLERANCE * Math.max(Math.abs(a), Math.abs(b)));

/** "summary.<metric>.<key>" for every place the record's summary disagrees with its repetitions. */
export function summaryMismatches(record, recomputed = recomputeSummary(record)) {
  const mismatches = [];
  for (const metric of METRICS) {
    const actual = record.summary?.[metric] ?? null;
    const expected = recomputed[metric];
    if (expected === null || actual === null) {
      if (expected !== actual) mismatches.push(`summary.${metric}`);
      continue;
    }
    for (const key of ['median', 'iqr', 'min', 'max', 'n']) {
      const same = key === 'n' ? actual[key] === expected[key] : typeof actual[key] === 'number' && close(actual[key], expected[key]);
      if (!same) mismatches.push(`summary.${metric}.${key}`);
    }
  }
  return mismatches;
}

/** One method name when prefill and decode used the same one throughout; otherwise both, or nothing if mixed. */
function timingMethod(complete) {
  const prefill = new Set(complete.map((attempt) => attempt.prefillMethod));
  const decode = new Set(complete.map((attempt) => attempt.decodeMethod));
  if (prefill.size !== 1 || decode.size !== 1) return undefined;
  const [p] = prefill;
  const [d] = decode;
  if (typeof p !== 'string' || typeof d !== 'string') return undefined;
  return p === d ? p : `prefill ${p}, decode ${d}`;
}

const spread = (agg) => (agg ? { iqr: round1(agg.iqr), min: round1(agg.min), max: round1(agg.max) } : undefined);

/**
 * Contract build: runtimes and versions from runtimeVersions (contract 1.2), plus the
 * harness commit and the installed APK. Without runtimeVersions, a llama.cpp session's
 * own "llama.rn <version>" label is its binding. Unknown values are absent.
 */
function rowBuild(record, capture, where, appIdentity) {
  const fromRuntimes = buildFromSession(record, where) ?? {};
  const build = { ...fromRuntimes };
  if (!build.runtimes && record.engine === 'llama.cpp') {
    const distribution = record.effectiveSettings?.distribution;
    if (distribution && typeof distribution.version === 'string') build.bindingVersion = `llama.rn ${distribution.version}`;
    else if (/^llama\.rn [0-9][0-9A-Za-z.+-]{0,39}$/.test(String(record.engineVersion))) build.bindingVersion = record.engineVersion;
  }
  const after = capture.appIdentity?.after;
  if (appIdentity === 'matched' && Array.isArray(after?.apks) && after.apks.length === 1) {
    build.apkSha256 = after.apks[0].sha256;
  }
  if (record.build?.status === 'reported' && typeof record.build.gitCommit === 'string') {
    build.harnessCommit = record.build.gitCommit;
  }
  const ordered = {};
  for (const key of ['engineVersion', 'bindingVersion', 'apkSha256', 'harnessCommit', 'runtimes']) {
    if (build[key] !== undefined) ordered[key] = build[key];
  }
  return Object.keys(ordered).length ? ordered : undefined;
}

/**
 * Contract 1.2 configurationId/configurationSha256: the configuration the session ran. Both
 * come from the reviewed configuration file publication_policy.py matched (facts
 * configurationFile: the session's configurationSource hash names a committed file under
 * xebenchapp/configs/ and the session carries exactly its content). A session that names no
 * such file, or whose configuration differs from it, names no configuration, so the board
 * (configs/board.json) never displays it. A value without the contract's form is absent.
 */
function rowConfiguration(record, facts) {
  const file = facts?.configurationFile;
  const id = record.configuration?.id;
  if (!isObject(file) || file.id !== id || typeof id !== 'string' || !CONFIGURATION_ID.test(id)) return {};
  const sha = record.configurationSource?.sha256;
  if (sha !== file.sha256 || !HEX64.test(String(sha))) return {};
  return { configurationId: id, configurationSha256: sha };
}

function asStated(record, n, decision) {
  // decodeMode fixed: every repetition decoded exactly maxDecodeTokens, past the end of turn.
  const decode = `decode ${record.protocol.maxDecodeTokens} tok${record.protocol.decodeMode === 'fixed' ? ', end of turn ignored' : ''}`;
  const parts = [`n=${n} cold-run median`, `prompt ${record.protocol.promptLabel}`, decode];
  const pm = record.coldRuns.find((attempt) => attempt.status === 'complete')?.prefillMethod;
  if (pm && pm !== 'engine-timings') parts.push(`prefill ${pm} (no engine counter)`);
  const open = decision.checks.filter((check) => check.outcome !== 'pass');
  parts.push(`${decision.policy} ${decision.validation}` +
    (open.length ? `: ${open.map((check) => `${check.check} ${check.outcome} (${check.detail})`).join('; ')}` : ''));
  return parts.join('; ');
}

/**
 * The board row a publishable session gives. Every number is recomputed from the
 * bundle's repetitions; validation and guardsPassed are the policy decision.
 */
export function bundleRow({ doc, capture, decision, facts, bundleSha256, chipNames = new Map() }) {
  const record = doc.record;
  const where = `${bundleSha256.slice(0, 12)}…/sessions/${doc.sessionId}.json`;
  const stats = recomputeSummary(record);
  const mismatches = summaryMismatches(record, stats);
  if (mismatches.length) {
    throw new PublicationError(`${where}: the record summary disagrees with its repetitions (${mismatches.join(', ')})`);
  }
  const complete = record.coldRuns.filter((attempt) => attempt.status === 'complete');
  const n = complete.length;
  for (const metric of METRICS) {
    if (stats[metric] && stats[metric].n !== n) throw new PublicationError(`${where}: ${metric} has ${stats[metric].n} of ${n} repetitions`);
  }
  if (!n || !stats.decodeTps || stats.decodeTps.median <= 0 || !stats.prefillTps || stats.prefillTps.median <= 0) {
    throw new PublicationError(`${where}: no positive measured prefill and decode speed`);
  }
  const dispersion = { ppTps: spread(stats.prefillTps), tgTps: spread(stats.decodeTps) };
  const device = doc.device ?? {};
  const row = {
    engine: record.engine,
    backend: record.backend,
    platform: record.platform,
    device: friendlyDevice(capture.device?.manufacturer ?? device.manufacturer, device.model),
    soc: device.soc || undefined,
    chipName: chipNames.get(device.model),
    model: record.model,
    quant: record.quant || undefined,
    quantBits: quantBitsFor(record.quant) ?? undefined,
    ppTps: round1(stats.prefillTps.median),
    tgTps: round1(stats.decodeTps.median),
    ttftMs: round1(stats.ttftMs?.median),
    provenance: 'measured',
    sourceName: SOURCE_NAME,
    sourceUrl: SOURCE_URL,
    sourceDate: String(record.timestampIso).slice(0, 7),
    sampleCount: n,
    asStated: asStated(record, n, decision),
    validation: decision.validation,
    guardsPassed: decision.guardsPassed,
    conditionPolicy: typeof record.conditionPolicy?.version === 'string' ? record.conditionPolicy.version : undefined,
    protocolVersion: record.protocol.version,
    ...rowConfiguration(record, facts),
    timingMethod: timingMethod(complete),
    dispersion,
    evidence: { sessionIds: [doc.sessionId], bundleSha256, url: evidenceUrl(bundleSha256) },
    build: rowBuild(record, capture, where, facts?.appIdentity),
  };
  return JSON.parse(JSON.stringify(row)); // drop undefined fields
}

/** True when `bundleDir` is <root>/evidence/<sha>, the path its evidence URL serves. */
export function isHosted(bundleDir, root, sha) {
  try {
    return realpathSync(bundleDir) === realpathSync(join(root, 'evidence', sha));
  } catch {
    return false;
  }
}

/**
 * Every indexed session must still come out of its hosted bundle as exactly the row the
 * index keeps: the bundle exists at <root>/evidence/<sha>/, verifies, and the policy and
 * recomputation give the same row. A hand-edited row, a rewritten session or a missing
 * bundle is an error. Returns "path: message" errors.
 */
export function reproductionErrors(index, root) {
  const errors = [];
  const chipNames = loadChipNames(root);
  const bySha = new Map();
  for (const entry of index.keys) {
    for (const session of entry.sessions) {
      bySha.set(session.bundleSha256, [...(bySha.get(session.bundleSha256) ?? []), { key: entry.key, session }]);
    }
  }
  for (const [sha, sessions] of [...bySha].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) {
    const dir = join(root, 'evidence', sha);
    const label = `evidence/${sha}`;
    try {
      if (!HEX64.test(sha) || !isHosted(dir, root, sha)) throw new PublicationError(`${label}: bundle is not hosted`);
      const report = runPolicy(dir, sha);
      if (!report.valid) throw new PublicationError(`${label}: bundle no longer verifies: ${report.errors.slice(0, 3).join('; ')}`);
      const bound = readBoundBundle(dir, sha);
      for (const { key, session } of sessions) {
        const item = report.sessions.find((candidate) => candidate.sessionId === session.sessionId);
        if (!item) {
          errors.push(`${label}: indexed session ${session.sessionId} (${key}) is not in its bundle`);
          continue;
        }
        const doc = bound.file(`sessions/${item.sessionId}.json`);
        const capture = bound.file(`captures/${doc.captureId}.json`);
        const row = item.decision.publishable
          ? bundleRow({ doc, capture, decision: item.decision, facts: item.facts, bundleSha256: sha, chipNames }) : null;
        // timestampIso orders the key's history and so picks the displayed row: it is
        // re-derived too, like the row itself.
        if (!row || !jsonEqual(row, session.row) || session.timestampIso !== doc.record.timestampIso) {
          errors.push(`data/sessions/index.json: session ${session.sessionId} (${key}) does not reproduce from ${label}`);
        }
      }
    } catch (error) {
      if (!(error instanceof PublicationError)) throw error;
      errors.push(error.message);
    }
  }
  return errors;
}
