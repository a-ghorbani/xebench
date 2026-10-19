#!/usr/bin/env node
/**
 * check_public_text.mjs — the lab-book gate for board text (docs/STYLE.md, Board text).
 *
 *   node scripts/check_public_text.mjs
 *   node scripts/check_public_text.mjs [--benchmarks <file>] [--findings <file>] [--allowlist <file>]
 *
 * Scans every string pocketpal.dev renders from xebench data:
 *   data/findings.json   each finding's headline and deck;
 *   data/benchmarks.json each row's displayed fields: engine, backend, platform, device, soc,
 *                        model, quant, sourceName, sourceDate, configurationId and the build
 *                        versions; plus asStated on vendor and community rows. A lab row's
 *                        asStated is lab-book provenance the site does not render, so it is exempt.
 * and fails on lab-book words (internal labels, process and history narration) and on 40- or
 * 64-character hex strings (commit and SHA-256 hashes). A justified exception is an entry in
 * configs/public-text-allowlist.json naming the file, the place, the matched text and a reason;
 * an entry that no longer matches anything fails too, so the list stays small and current.
 *
 * Exit codes: 0 clean, 1 findings (each printed with where and why), 2 usage.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/** [pattern, why] — matched case-insensitively on word boundaries. */
export const BANNED = Object.freeze([
  ['legacy', 'internal label'],
  ['harness', 'internal label (say "the benchmark app" or name the tool)'],
  ['locked', 'process narration'],
  ['pending', 'process narration: say what is true now'],
  ['gat(e|es|ed|ing)', 'internal label (say what was checked, e.g. "output check")'],
  ['epoch', 'internal label'],
  ['previously', 'history narration'],
  ['marked( as)? failed', 'history narration'],
  ['withdrawn', 'history narration'],
  ['superseded', 'history narration'],
  ['corrected', 'history narration (the history goes in docs/CORRECTIONS.md)'],
  ['correction', 'history narration (the history goes in docs/CORRECTIONS.md)'],
  ['retired', 'internal label'],
  ['staged', 'internal label'],
  ['carr(y|ied)[ -]?over', 'internal label'],
  ['roadmap(ped)?', 'process narration'],
  ['todo|tbd|wip', 'unfinished copy'],
  ['lab[ -]book', 'internal label'],
]);
const HEX = /\b(?:[0-9a-f]{64}|[0-9a-f]{40})\b/gi;
const ROW_FIELDS = ['engine', 'backend', 'platform', 'device', 'soc', 'model', 'quant', 'sourceName', 'sourceDate', 'configurationId'];
const BUILD_FIELDS = ['engineVersion', 'bindingVersion'];
const ALLOWLIST_SCHEMA = 'xebench-public-text-allowlist';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const args = process.argv.slice(2);
const paths = {
  '--benchmarks': `${ROOT}data/benchmarks.json`,
  '--findings': `${ROOT}data/findings.json`,
  '--allowlist': `${ROOT}configs/public-text-allowlist.json`,
};
for (let index = 0; index < args.length; index += 2) {
  if (!Object.hasOwn(paths, args[index]) || args[index + 1] === undefined) {
    console.error('usage: node scripts/check_public_text.mjs [--benchmarks <file>] [--findings <file>] [--allowlist <file>]');
    process.exit(2);
  }
  paths[args[index]] = args[index + 1];
}

const read = (path) => {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    console.error(`error: cannot read ${path}: ${error.message}`);
    process.exit(1);
  }
};
const key = (row) => [row.engine, row.backend, row.platform, row.device, row.model, row.quant, row.provenance].join('|');

/** Every rendered string: { file, where, text }. */
export function renderedText(benchmarks, findings) {
  const out = [];
  for (const finding of findings.findings ?? []) {
    for (const field of ['headline', 'deck']) {
      if (typeof finding[field] === 'string') out.push({ file: 'data/findings.json', where: `${finding.id}.${field}`, text: finding[field] });
    }
  }
  for (const row of benchmarks.rows ?? []) {
    const fields = [...ROW_FIELDS, ...(row.provenance === 'measured' ? [] : ['asStated'])];
    for (const field of fields) {
      if (typeof row[field] === 'string') out.push({ file: 'data/benchmarks.json', where: `${key(row)}.${field}`, text: row[field] });
    }
    for (const field of BUILD_FIELDS) {
      if (typeof row.build?.[field] === 'string') {
        out.push({ file: 'data/benchmarks.json', where: `${key(row)}.build.${field}`, text: row.build[field] });
      }
    }
    (row.build?.runtimes ?? []).forEach((runtime, index) => {
      for (const field of ['name', 'version']) {
        if (typeof runtime?.[field] === 'string') {
          out.push({ file: 'data/benchmarks.json', where: `${key(row)}.build.runtimes[${index}].${field}`, text: runtime[field] });
        }
      }
    });
  }
  return out;
}

/** Every lab-book hit in the rendered strings: { file, where, match, why, text }. */
export function hits(items) {
  const out = [];
  for (const item of items) {
    for (const [pattern, why] of BANNED) {
      for (const match of item.text.matchAll(new RegExp(`\\b(?:${pattern})\\b`, 'gi'))) {
        out.push({ ...item, match: match[0], why });
      }
    }
    for (const match of item.text.matchAll(HEX)) out.push({ ...item, match: match[0], why: 'inline hash' });
  }
  return out;
}

function allowlistErrors(allowlist) {
  const errors = [];
  if (allowlist?.schema !== ALLOWLIST_SCHEMA || allowlist?.schemaVersion !== 1 || !Array.isArray(allowlist?.entries)) {
    return [`${paths['--allowlist']}: needs schema ${ALLOWLIST_SCHEMA}, schemaVersion 1 and an entries array`];
  }
  allowlist.entries.forEach((entry, index) => {
    for (const field of ['file', 'where', 'match', 'reason']) {
      if (typeof entry?.[field] !== 'string' || !entry[field].trim()) errors.push(`allowlist entries[${index}]: ${field} must be a non-empty string`);
    }
    for (const field of Object.keys(entry ?? {})) {
      if (!['file', 'where', 'match', 'reason'].includes(field)) errors.push(`allowlist entries[${index}]: unknown field ${field}`);
    }
  });
  return errors;
}

const allowlist = read(paths['--allowlist']);
const errors = allowlistErrors(allowlist);
if (errors.length) {
  for (const error of errors) console.error(`error: ${error}`);
  process.exit(1);
}
const found = hits(renderedText(read(paths['--benchmarks']), read(paths['--findings'])));
const allowed = (hit, entry) => entry.file === hit.file && entry.where === hit.where && entry.match.toLowerCase() === hit.match.toLowerCase();
const blocking = found.filter((hit) => !allowlist.entries.some((entry) => allowed(hit, entry)));
const stale = allowlist.entries.filter((entry) => !found.some((hit) => allowed(hit, entry)));
for (const hit of blocking) {
  const excerpt = hit.text.length > 120 ? `${hit.text.slice(0, 117)}...` : hit.text;
  console.error(`${hit.file} ${hit.where}: "${hit.match}" (${hit.why}) in ${JSON.stringify(excerpt)}`);
}
for (const entry of stale) {
  console.error(`configs/public-text-allowlist.json: entry for ${entry.file} ${entry.where} "${entry.match}" matches nothing; remove it`);
}
if (blocking.length || stale.length) {
  console.error(`\n${blocking.length} lab-book term(s) in board text. A visitor reads this text cold: rewrite it as the current ` +
    'state in plain words (docs/STYLE.md, Board text), or add a justified exception to configs/public-text-allowlist.json.');
  process.exit(1);
}
console.error(`board text is clean: ${renderedText(read(paths['--benchmarks']), read(paths['--findings'])).length} rendered strings checked`);
