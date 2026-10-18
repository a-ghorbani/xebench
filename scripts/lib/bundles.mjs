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
  assertFinite, buildFromSession, evidenceUrl, friendlyDevice, PublicationError,
} from './publication.mjs';

export const POLICY_SCRIPT = fileURLToPath(new URL('../publication_policy.py', import.meta.url));
export const POLICY_VERSION = 'publication-policy-v1';
export const METRICS = Object.freeze(['loadMs', 'ttftMs', 'prefillTps', 'decodeTps']);
// raw_session.TOLERANCE: summaries are compared with the validator's tolerance.
const TOLERANCE = 1e-12;
const HEX64 = /^[0-9a-f]{64}$/;
const SOURCE_NAME = 'PocketPal R&D lab (xebench)';
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
function rowBuild(record, capture, where) {
  const fromRuntimes = buildFromSession(record, where) ?? {};
  const build = { ...fromRuntimes };
  if (!build.runtimes && record.engine === 'llama.cpp') {
    const distribution = record.effectiveSettings?.distribution;
    if (distribution && typeof distribution.version === 'string') build.bindingVersion = `llama.rn ${distribution.version}`;
    else if (/^llama\.rn [0-9][0-9A-Za-z.+-]{0,39}$/.test(String(record.engineVersion))) build.bindingVersion = record.engineVersion;
  }
  const after = capture.appIdentity?.after;
  if (capture.appIdentity?.consistency === 'matched' && Array.isArray(after?.apks) && after.apks.length === 1) {
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

function asStated(record, n, decision) {
  const parts = [`n=${n} cold-run median`, `prompt ${record.protocol.promptLabel}`, `decode ${record.protocol.maxDecodeTokens} tok`];
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
export function bundleRow({ doc, capture, decision, bundleSha256 }) {
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
    model: record.model,
    quant: record.quant || undefined,
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
    timingMethod: timingMethod(complete),
    dispersion,
    evidence: { sessionIds: [doc.sessionId], bundleSha256, url: evidenceUrl(bundleSha256) },
    build: rowBuild(record, capture, where),
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
