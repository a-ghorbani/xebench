/**
 * Shared, dependency-free validation for the published data files.
 *
 * `validateSchema` implements only the JSON Schema 2020-12 keywords that
 * data/*.schema.json use. Any other keyword is rejected so that a schema change
 * cannot silently bypass validation; extend this file when a schema needs more.
 */
import {
  chmodSync, closeSync, fsyncSync, openSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync,
} from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';

const ANNOTATIONS = new Set(['$schema', '$id', '$comment', '$defs', 'title', 'description', 'default', 'examples']);
const ASSERTIONS = new Set([
  'type', 'enum', 'const', 'required', 'properties', 'additionalProperties', 'items', '$ref', 'minimum', 'format',
  'pattern', 'minLength', 'maxLength', 'minItems', 'uniqueItems', 'allOf', 'if', 'then', 'else',
]);

export class PublicationError extends Error {}

const typeOf = (value) => {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
};

const matchesType = (value, type) => {
  switch (type) {
    case 'null': return value === null;
    case 'boolean': return typeof value === 'boolean';
    case 'string': return typeof value === 'string';
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'integer': return Number.isInteger(value);
    case 'array': return Array.isArray(value);
    case 'object': return typeOf(value) === 'object';
    default: throw new PublicationError(`unsupported schema type ${JSON.stringify(type)}`);
  }
};

/** JSON value equality (const/enum): same type and structure. */
export const jsonEqual = (a, b) => {
  if (a === b) return true;
  if (typeOf(a) !== typeOf(b) || typeof a !== 'object' || a === null) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((item, index) => jsonEqual(item, b[index]));
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && jsonEqual(a[key], b[key]));
};

const compilePattern = (pattern, path) => {
  if (typeof pattern !== 'string') throw new PublicationError(`pattern at ${path} must be a string`);
  try {
    return new RegExp(pattern, 'u');
  } catch (error) {
    throw new PublicationError(`invalid pattern at ${path}: ${error.message}`);
  }
};

// Strict absolute URI per the RFC 3986 grammar (as ajv-formats "uri" checks it):
// only unreserved/sub-delim characters and valid %XX escapes in each component,
// brackets only around an IP literal, one '#'. WHATWG URL then also has to parse
// it, so this is never looser than a stock RFC 3986 validator.
const PCT = '%[0-9a-f]{2}';
const SUB = "a-z0-9\\-._~!$&'()*+,;=";
const PCHAR = `(?:[${SUB}:@]|${PCT})`;
const AUTHORITY = `(?:(?:[${SUB}:]|${PCT})*@)?(?:\\[[0-9a-f:.]+\\]|\\[v[0-9a-f]+\\.[${SUB}:]+\\]|(?:[${SUB}]|${PCT})*)(?::[0-9]*)?`;
const URI = new RegExp(
  `^[a-z][a-z0-9+\\-.]*:(?://${AUTHORITY}(?:/${PCHAR}*)*|/(?:${PCHAR}+(?:/${PCHAR}*)*)?|${PCHAR}+(?:/${PCHAR}*)*)` +
    `(?:\\?(?:${PCHAR}|[/?])*)?(?:#(?:${PCHAR}|[/?])*)?$`,
  'i',
);
const isUri = (value) => {
  try {
    return URI.test(value) && Boolean(new URL(value));
  } catch {
    return false;
  }
};

const resolveRef = (root, ref) => {
  if (!ref.startsWith('#/')) throw new PublicationError(`unsupported $ref ${ref}`);
  let node = root;
  for (const part of ref.slice(2).split('/')) {
    const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
    if (node == null || typeof node !== 'object' || !Object.hasOwn(node, key)) {
      throw new PublicationError(`unresolvable $ref ${ref}`);
    }
    node = node[key];
  }
  return node;
};

const TYPES = new Set(['null', 'boolean', 'string', 'number', 'integer', 'array', 'object']);
const SUBSCHEMA_MAPS = ['$defs', 'properties'];

/** Eagerly reject keywords, types and shapes this validator does not implement, anywhere in the schema. */
export function checkSchema(schema, path = '#') {
  if (typeof schema === 'boolean') return;
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
    throw new PublicationError(`schema at ${path} must be an object or boolean`);
  }
  for (const keyword of Object.keys(schema)) {
    if (!ANNOTATIONS.has(keyword) && !ASSERTIONS.has(keyword)) {
      throw new PublicationError(`unsupported schema keyword ${keyword} at ${path}`);
    }
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    for (const type of types) {
      if (!TYPES.has(type)) throw new PublicationError(`unsupported schema type ${JSON.stringify(type)} at ${path}`);
    }
  }
  if (schema.enum !== undefined && !Array.isArray(schema.enum)) throw new PublicationError(`enum at ${path} must be an array`);
  if (schema.required !== undefined && !Array.isArray(schema.required)) {
    throw new PublicationError(`required at ${path} must be an array`);
  }
  if (schema.minimum !== undefined && typeof schema.minimum !== 'number') {
    throw new PublicationError(`minimum at ${path} must be a number`);
  }
  for (const key of ['minLength', 'maxLength', 'minItems']) {
    if (schema[key] !== undefined && !(Number.isInteger(schema[key]) && schema[key] >= 0)) {
      throw new PublicationError(`${key} at ${path} must be a non-negative integer`);
    }
  }
  if (schema.pattern !== undefined) compilePattern(schema.pattern, path);
  if (schema.uniqueItems !== undefined && typeof schema.uniqueItems !== 'boolean') {
    throw new PublicationError(`uniqueItems at ${path} must be a boolean`);
  }
  if ((schema.then !== undefined || schema.else !== undefined) && schema.if === undefined) {
    throw new PublicationError(`then/else without if at ${path}`);
  }
  if (schema.allOf !== undefined) {
    if (!Array.isArray(schema.allOf) || schema.allOf.length === 0) {
      throw new PublicationError(`allOf at ${path} must be a non-empty array`);
    }
    schema.allOf.forEach((sub, index) => checkSchema(sub, `${path}/allOf/${index}`));
  }
  if (schema.format !== undefined && schema.format !== 'uri') {
    throw new PublicationError(`unsupported format ${schema.format} at ${path}`);
  }
  if (schema.$ref !== undefined && (typeof schema.$ref !== 'string' || !schema.$ref.startsWith('#/'))) {
    throw new PublicationError(`unsupported $ref ${schema.$ref} at ${path}`);
  }
  for (const map of SUBSCHEMA_MAPS) {
    for (const [key, sub] of Object.entries(schema[map] ?? {})) checkSchema(sub, `${path}/${map}/${key}`);
  }
  for (const key of ['items', 'additionalProperties', 'if', 'then', 'else']) {
    if (schema[key] !== undefined) checkSchema(schema[key], `${path}/${key}`);
  }
}

/** Returns a list of "path: message" errors; empty when `value` conforms. */
export function validateSchema(value, schema, root = schema, path = '$', errors = []) {
  checkSchema(root);
  return validate(value, schema, root, path, errors);
}

function validate(value, schema, root, path, errors) {
  if (typeof schema === 'boolean') {
    if (!schema) errors.push(`${path}: not allowed`);
    return errors;
  }
  for (const keyword of Object.keys(schema)) {
    if (!ANNOTATIONS.has(keyword) && !ASSERTIONS.has(keyword)) {
      throw new PublicationError(`unsupported schema keyword ${keyword} at ${path}`);
    }
  }
  if (schema.$ref) validate(value, resolveRef(root, schema.$ref), root, path, errors);
  for (const sub of schema.allOf ?? []) validate(value, sub, root, path, errors);
  if (schema.if !== undefined) {
    const branch = validate(value, schema.if, root, path, []).length === 0 ? schema.then : schema.else;
    if (branch !== undefined) validate(value, branch, root, path, errors);
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => matchesType(value, type))) {
      errors.push(`${path}: expected ${types.join('|')}, got ${typeOf(value)}`);
      return errors;
    }
  }
  if (schema.enum && !schema.enum.some((item) => jsonEqual(item, value))) {
    errors.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
  }
  if (schema.const !== undefined && !jsonEqual(schema.const, value)) {
    errors.push(`${path}: ${JSON.stringify(value)} is not ${JSON.stringify(schema.const)}`);
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && [...value].length < schema.minLength) {
      errors.push(`${path}: shorter than ${schema.minLength} characters`);
    }
    if (schema.maxLength !== undefined && [...value].length > schema.maxLength) {
      errors.push(`${path}: longer than ${schema.maxLength} characters`);
    }
    if (schema.pattern !== undefined && !compilePattern(schema.pattern, path).test(value)) {
      errors.push(`${path}: ${JSON.stringify(value)} does not match ${schema.pattern}`);
    }
  }
  if (Array.isArray(value) && schema.minItems !== undefined && value.length < schema.minItems) {
    errors.push(`${path}: fewer than ${schema.minItems} items`);
  }
  if (Array.isArray(value) && schema.uniqueItems === true &&
      value.some((item, index) => value.slice(0, index).some((earlier) => jsonEqual(earlier, item)))) {
    errors.push(`${path}: items are not unique`);
  }
  if (schema.minimum !== undefined && typeof value === 'number' && value < schema.minimum) {
    errors.push(`${path}: ${value} < minimum ${schema.minimum}`);
  }
  if (schema.format !== undefined) {
    if (schema.format !== 'uri') throw new PublicationError(`unsupported format ${schema.format}`);
    if (typeof value === 'string' && !isUri(value)) errors.push(`${path}: not an absolute URI`);
  }
  if (typeOf(value) === 'object') {
    for (const key of schema.required ?? []) {
      if (!Object.hasOwn(value, key)) errors.push(`${path}: missing required ${key}`);
    }
    const properties = schema.properties ?? {};
    for (const [key, item] of Object.entries(value)) {
      if (Object.hasOwn(properties, key)) validate(item, properties[key], root, `${path}.${key}`, errors);
      else if (schema.additionalProperties !== undefined) {
        validate(item, schema.additionalProperties, root, `${path}.${key}`, errors);
      }
    }
  }
  if (Array.isArray(value) && schema.items !== undefined) {
    value.forEach((item, index) => validate(item, schema.items, root, `${path}[${index}]`, errors));
  }
  return errors;
}

export function assertSchema(value, schema, label, root = schema, extraErrors = []) {
  const errors = [...validateSchema(value, schema, root), ...extraErrors];
  if (errors.length) {
    const shown = errors.slice(0, 20).join('\n  ');
    const more = errors.length > 20 ? `\n  ... ${errors.length - 20} more` : '';
    throw new PublicationError(`${label} does not match its schema/contract:\n  ${shown}${more}`);
  }
}

/** Throws on NaN/Infinity anywhere (JSON.parse turns overflowing exponents into Infinity). */
export function assertFinite(value, path = '$') {
  if (typeof value === 'number' && !Number.isFinite(value)) {
    throw new PublicationError(`${path}: non-finite number`);
  }
  if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) assertFinite(item, `${path}.${key}`);
  }
}

/** Strict JSON read: unreadable, unparseable or non-finite content is an error. */
export function readJson(path, label = String(path)) {
  let text;
  try {
    text = readFileSync(path, 'utf8');
  } catch (error) {
    throw new PublicationError(`cannot read ${label}: ${error.message}`);
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new PublicationError(`${label} is not valid JSON: ${error.message}`);
  }
  assertFinite(value, label);
  return value;
}

/**
 * Row identity. Unchanged from the original publication contract: re-running
 * the same engine/backend/platform/device/model/quant/provenance replaces a row.
 */
export const rowKey = (row) =>
  [row.engine, row.backend, row.platform, row.device, row.model, row.quant, row.provenance].join('|');

/**
 * The stable public URL of a hosted evidence bundle: its manifest under evidence/<sha>/
 * on main (evidence/README.md). Every row's evidence.url comes from this one function.
 */
export const EVIDENCE_BASE_URL = 'https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence';
export const evidenceUrl = (bundleSha256) => {
  if (typeof bundleSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(bundleSha256)) {
    throw new PublicationError(`not a bundle SHA-256: ${JSON.stringify(bundleSha256)}`);
  }
  return `${EVIDENCE_BASE_URL}/${bundleSha256}/manifest.json`;
};

// Marketing names for lab devices (raw ro.product.model -> friendly). Lab rows
// bypass the website's crowd device-name resolver, so map here.
const DEVICE_NAMES = {
  'Pixel 9': 'Google Pixel 9',
  'SM-S911B': 'Samsung Galaxy S23',
  'ONEPLUS A6003': 'OnePlus 6',
  '25102PCBEG': 'POCO F8 Ultra',
  '2511FPC34G': 'POCO X8 Pro',
  '22126RN91Y': 'Redmi Note 12',
};
export const friendlyDevice = (mfr, model) => {
  if (DEVICE_NAMES[model]) return DEVICE_NAMES[model];
  return [mfr, model].filter(Boolean).join(' ').trim() || 'unknown';
};

const ORDER = ['platform', 'device', 'model', 'engine', 'backend', 'quant', 'provenance'];
const codepoint = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * Total, locale-independent row order: platform, device, model, engine, then the
 * remaining key fields as tie-breakers. Any input order yields the same output.
 */
export function compareRows(a, b) {
  for (const field of ORDER) {
    const diff = codepoint(String(a[field] ?? ''), String(b[field] ?? ''));
    if (diff) return diff;
  }
  return 0;
}

/** The contract version this producer writes (data/benchmarks.schema.json). */
export const CONTRACT_VERSION = '1.1';
/** The minor that adds build.runtimes and the configuration fields; a file declares it only when a row carries one. */
export const RUNTIMES_CONTRACT_VERSION = '1.2';
/** The minor that adds dateBasis on rows without versions; a file declares it only when a row carries one. */
export const DATE_BASIS_CONTRACT_VERSION = '1.3';
/**
 * quantBits (contract 1.3): the weight bit width a quant scheme names, where it is certain; null
 * otherwise. The board shows "4-bit" with the scheme as secondary text. A row's quantBits must
 * equal quantBitsFor(quant).
 */
export function quantBitsFor(quant) {
  if (typeof quant !== 'string') return null;
  if (/^Q4_(0|1|K_[SM])( \(QAT\))?$/.test(quant)) return 4;
  if (/^Q8(_0)?$/.test(quant)) return 8;
  if (/^(BF16|F16)$/.test(quant)) return 16;
  if (/^(int4|W4A16|SpinQuant 4-bit|4-bit groupwise|4-bit groupwise \+ 4-bit embedding|4-bit QAT|4-bit QAT \+ LoRA)$/.test(quant)) return 4;
  return null;
}

/** Minors at or above the producer's floor: a file at one of them needs no 1.1 migration. */
const MIGRATED_VERSIONS = new Set([CONTRACT_VERSION, RUNTIMES_CONTRACT_VERSION, DATE_BASIS_CONTRACT_VERSION]);
/**
 * dateBasis (contract 1.3): what a legacy row's sourceDate is. "measured": the month the row
 * was measured; "first-published": the month the row was first published, used only when the
 * measurement month cannot be established. Legacy rows only, with sourceDate. Not displayed.
 */
export const DATE_BASES = Object.freeze(['measured', 'first-published']);

/** Closed list of build.runtimes names (contract 1.2). */
export const RUNTIME_NAMES = Object.freeze([
  'llama.cpp', 'llama.rn', 'LiteRT-LM', 'litert-npu-runtime-qualcomm', 'QAIRT', 'geniex-android',
]);
/** build.runtimes[].version: a llama.cpp build (b11385) or a dotted version with an optional pre-release. */
const DOTTED = '[0-9]{1,5}(\\.[0-9]{1,12}){1,3}(-[0-9A-Za-z]{1,16}(\\.[0-9A-Za-z]{1,16}){0,3})?';
export const RUNTIME_VERSION = new RegExp(`^(b[0-9]{1,7}|${DOTTED})$`);
const VERSION = new RegExp(`^${DOTTED}$`);
const HEX_COMMIT = /^[0-9a-f]{7,40}$/;

/** Row fields introduced in 1.1; valid on provenance=measured rows only. */
export const MEASURED_ONLY_FIELDS = Object.freeze([
  'validation', 'guardsPassed', 'conditionPolicy', 'protocolVersion', 'timingMethod', 'dispersion', 'evidence', 'build',
]);

/**
 * Row fields 1.2 adds next to protocolVersion: the run configuration a bundle session ran.
 * Measured rows from evidence bundles only (they need evidence), never on legacy rows.
 */
export const CONFIGURATION_FIELDS = Object.freeze(['configurationId', 'configurationSha256']);
/** configurationId: a configuration file's id, e.g. cpu-reference-v2. */
export const CONFIGURATION_ID = /^[a-z0-9][a-z0-9.-]{0,63}$/;

/** Full ISO-8601 UTC timestamp without fractional seconds: YYYY-MM-DDTHH:MM:SSZ. */
export const isoNow = (date = new Date()) => date.toISOString().replace(/\.\d{3}Z$/, 'Z');

const isObject = (value) => typeOf(value) === 'object';

const isCalendarTimestamp = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})Z)?$/.exec(value);
  if (!match) return false;
  const [year, month, day, hour, minute, second] = match.slice(1).map((part) => Number(part ?? 0));
  const date = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day &&
    date.getUTCHours() === hour && date.getUTCMinutes() === minute && date.getUTCSeconds() === second;
};

/**
 * Cross-field contract invariants for one row, as "path: message" errors. The
 * schema expresses most of them (for consumers using a stock validator); this is
 * the authoritative check, and also covers what the schema cannot express.
 */
export function rowInvariantErrors(row, path = '$') {
  const errors = [];
  if (row.provenance !== 'measured') {
    for (const field of [...MEASURED_ONLY_FIELDS, ...CONFIGURATION_FIELDS]) {
      if (Object.hasOwn(row, field)) errors.push(`${path}: ${field} is only valid on provenance=measured rows`);
    }
  }
  if (row.validation === 'verified') {
    if (row.guardsPassed !== true) errors.push(`${path}: validation=verified requires guardsPassed === true`);
    if (!Object.hasOwn(row, 'evidence')) errors.push(`${path}: validation=verified requires evidence`);
  }
  if (row.guardsPassed === false && row.validation !== 'failed') {
    errors.push(`${path}: guardsPassed=false requires validation=failed`);
  }
  // A measured row without validation (a 1.0 row) is legacy, so the same rule applies.
  if (row.validation === 'legacy' || (row.provenance === 'measured' && !Object.hasOwn(row, 'validation'))) {
    for (const field of ['dispersion', 'evidence', ...CONFIGURATION_FIELDS]) {
      if (Object.hasOwn(row, field)) errors.push(`${path}: legacy rows carry no ${field}`);
    }
  }
  if (Object.hasOwn(row, 'quantBits') && row.quantBits !== quantBitsFor(row.quant)) {
    errors.push(`${path}: quantBits ${row.quantBits} is not the bit width of quant ${JSON.stringify(row.quant)} (${quantBitsFor(row.quant) ?? 'none certain'})`);
  }
  if (Object.hasOwn(row, 'dateBasis')) {
    if (row.provenance !== 'measured' || row.validation !== 'legacy') errors.push(`${path}: dateBasis is only valid on legacy rows`);
    if (!Object.hasOwn(row, 'sourceDate')) errors.push(`${path}: dateBasis requires sourceDate`);
  }
  if (CONFIGURATION_FIELDS.some((field) => Object.hasOwn(row, field))) {
    if (!Object.hasOwn(row, 'configurationId')) errors.push(`${path}: configurationSha256 requires configurationId`);
    if (!Object.hasOwn(row, 'evidence')) errors.push(`${path}: configurationId comes from an evidence bundle and requires evidence`);
  }
  const runtimes = isObject(row.build) && Array.isArray(row.build.runtimes) ? row.build.runtimes : [];
  const names = runtimes.map((item) => (isObject(item) ? item.name : undefined));
  const repeated = names.filter((name, index) => name !== undefined && names.indexOf(name) !== index);
  if (repeated.length) errors.push(`${path}.build.runtimes: more than one entry for ${[...new Set(repeated)].join(', ')}`);
  for (const [metric, spread] of Object.entries(isObject(row.dispersion) ? row.dispersion : {})) {
    if (isObject(spread) && typeof spread.min === 'number' && typeof spread.max === 'number' && spread.min > spread.max) {
      errors.push(`${path}.dispersion.${metric}: min ${spread.min} > max ${spread.max}`);
    }
  }
  return errors;
}

/** Schema plus envelope and row invariants the schema cannot express. */
export function assertBenchmarks(envelope, schema, label) {
  assertFinite(envelope, label);
  // An unsupported major is rejected on its own, before any field is interpreted.
  if (isObject(envelope) && String(envelope.schemaVersion).split('.')[0] !== '1') {
    throw new PublicationError(`${label}: unsupported schemaVersion ${envelope.schemaVersion}`);
  }
  // Contract invariants are reported with the schema errors: the schema rejects
  // most of them too, but these messages say which invariant was broken.
  const invariants = Array.isArray(envelope?.rows)
    ? envelope.rows.flatMap((row, index) => (isObject(row) ? rowInvariantErrors(row, `$.rows[${index}]`) : []))
    : [];
  if (envelope?.schemaVersion === '1.0' && Array.isArray(envelope.rows)) {
    envelope.rows.forEach((row, index) => {
      for (const field of MEASURED_ONLY_FIELDS) {
        if (isObject(row) && Object.hasOwn(row, field)) invariants.push(`$.rows[${index}]: ${field} needs schemaVersion 1.1`);
      }
    });
  }
  if ((envelope?.schemaVersion === '1.0' || envelope?.schemaVersion === '1.1') && Array.isArray(envelope.rows)) {
    envelope.rows.forEach((row, index) => {
      if (isObject(row) && isObject(row.build) && Object.hasOwn(row.build, 'runtimes')) {
        invariants.push(`$.rows[${index}]: build.runtimes needs schemaVersion ${RUNTIMES_CONTRACT_VERSION}`);
      }
      for (const field of [...CONFIGURATION_FIELDS, 'chipName']) {
        if (isObject(row) && Object.hasOwn(row, field)) {
          invariants.push(`$.rows[${index}]: ${field} needs schemaVersion ${RUNTIMES_CONTRACT_VERSION}`);
        }
      }
    });
  }
  if (['1.0', '1.1', '1.2'].includes(envelope?.schemaVersion) && Array.isArray(envelope.rows)) {
    envelope.rows.forEach((row, index) => {
      for (const field of ['dateBasis', 'quantBits']) {
        if (isObject(row) && Object.hasOwn(row, field)) {
          invariants.push(`$.rows[${index}]: ${field} needs schemaVersion ${DATE_BASIS_CONTRACT_VERSION}`);
        }
      }
    });
  }
  assertSchema(envelope, schema, label, schema, invariants);
  if (envelope.rowCount !== undefined && envelope.rowCount !== envelope.rows.length) {
    throw new PublicationError(`${label}: rowCount ${envelope.rowCount} != ${envelope.rows.length} rows`);
  }
  if (!isCalendarTimestamp(envelope.generatedAt)) {
    throw new PublicationError(`${label}: generatedAt ${JSON.stringify(envelope.generatedAt)} is not a real UTC date/time`);
  }
  const seen = new Set();
  for (const row of envelope.rows) {
    const key = rowKey(row);
    if (seen.has(key)) throw new PublicationError(`${label}: duplicate row key ${key}`);
    seen.add(key);
  }
}

/** Why `envelope` is not yet at CONTRACT_VERSION; empty when fully migrated. */
export function migrationNeeds(envelope) {
  const needs = [];
  if (!MIGRATED_VERSIONS.has(envelope.schemaVersion)) {
    needs.push(`schemaVersion is ${envelope.schemaVersion}, not ${CONTRACT_VERSION} or later`);
  }
  const unlabelled = envelope.rows.filter((row) => row.provenance === 'measured' && !Object.hasOwn(row, 'validation'));
  if (unlabelled.length) needs.push(`${unlabelled.length} measured row(s) carry no validation`);
  return needs;
}

/**
 * The 1.0 -> 1.1 migration. Every provenance=measured row without `validation`
 * gets validation "legacy" (appended, so its other fields keep their bytes);
 * every other row is the same object. Already-migrated input is returned
 * unchanged (`touched` 0, `changed` false), which makes the migration idempotent.
 */
export function migrateTo11(envelope, now = isoNow()) {
  if (envelope.schemaVersion !== '1.0' && !MIGRATED_VERSIONS.has(envelope.schemaVersion)) {
    throw new PublicationError(`cannot migrate schemaVersion ${JSON.stringify(envelope.schemaVersion)} (only 1.0 -> 1.1)`);
  }
  if (migrationNeeds(envelope).length === 0) return { envelope, touched: 0, changed: false };
  let touched = 0;
  const rows = envelope.rows.map((row) => {
    if (row.provenance !== 'measured' || Object.hasOwn(row, 'validation')) return row;
    touched += 1;
    return { ...row, validation: 'legacy' };
  });
  return { envelope: { ...envelope, schemaVersion: CONTRACT_VERSION, generatedAt: now, rows }, touched, changed: true };
}

/** Write `text` to a fresh temp file next to `target`, flush it and check it; returns the temp path. */
function stage(target, text) {
  const tmp = join(dirname(target), `.${randomUUID()}.${basename(target)}.tmp`);
  let fd;
  try {
    fd = openSync(tmp, 'wx', 0o644);
    writeFileSync(fd, text); // loops until every byte is written; throws on EFBIG/ENOSPC
    fsyncSync(fd);
    const written = fd;
    fd = undefined; // never close twice: a failed close must still reach the cleanup below
    closeSync(written);
    chmodSync(tmp, statSync(target).mode & 0o777);
    if (readFileSync(tmp, 'utf8') !== text) throw new PublicationError('temporary file does not match the output');
    return tmp;
  } catch (error) {
    try {
      if (fd !== undefined) closeSync(fd);
    } catch {
      // keep the original error; still remove the temp file
    }
    removeQuietly(tmp);
    throw error;
  }
}

function removeQuietly(path) {
  try {
    unlinkSync(path);
  } catch {
    // never created or already renamed
  }
}

function syncDirectory(path) {
  try {
    const dir = openSync(dirname(path), 'r');
    fsyncSync(dir); // persist the rename; best-effort where directories cannot be fsynced
    closeSync(dir);
  } catch {
    // not supported on this platform
  }
}

/**
 * Replace several files together: every new text is staged and checked first, every
 * target must still hold its `before` text, then each is renamed over its target in
 * order. If a later rename fails, the targets already replaced are restored to their
 * `before` text, so readers see the old set or the new set, never a mix that stays.
 */
export function writeAtomicGroup(items) {
  const staged = [];
  try {
    for (const item of items) staged.push({ ...item, tmp: stage(item.target, item.text) });
    for (const item of staged) {
      if (readFileSync(item.target, 'utf8') !== item.before) {
        throw new PublicationError(`${basename(item.target)} changed while it was being regenerated; re-run`);
      }
    }
  } catch (error) {
    staged.forEach((item) => removeQuietly(item.tmp));
    throw error;
  }
  const done = [];
  try {
    for (const item of staged) {
      renameSync(item.tmp, item.target);
      done.push(item);
      syncDirectory(item.target);
    }
  } catch (error) {
    staged.filter((item) => !done.includes(item)).forEach((item) => removeQuietly(item.tmp));
    const stuck = [];
    for (const item of done.reverse()) {
      try {
        writeAtomicGroup([{ target: item.target, text: item.before, before: item.text }]);
      } catch {
        stuck.push(item.target);
      }
    }
    if (!stuck.length) throw error;
    const failure = new PublicationError(
      `writing failed (${error.code ?? error.message}) and restoring the earlier content failed too: ` +
        `${stuck.map((path) => basename(path)).join(', ')} now ${stuck.length === 1 ? 'holds' : 'hold'} the new ` +
        'content while the rest are unchanged. ' +
        'Restore them (git checkout -- data/) before publishing again.',
    );
    failure.partialWrite = stuck;
    throw failure;
  }
}

/**
 * Write next to the target, flush, then rename over it: readers see old or new,
 * never partial. Refuses if the target no longer holds `expectedBefore`.
 */
export function writeAtomic(target, text, expectedBefore) {
  writeAtomicGroup([{ target, text, before: expectedBefore }]);
}

/**
 * The schemaVersion an envelope holding `rows` declares: 1.3 as soon as one row carries
 * dateBasis, else 1.2 as soon as one row carries build.runtimes or a configuration field, otherwise the producer's
 * CONTRACT_VERSION; never below `floor` (the version the canonical file already declares).
 */
export function contractVersionFor(rows, floor = CONTRACT_VERSION) {
  const needsRuntimes = rows.some((row) => (isObject(row.build) && Object.hasOwn(row.build, 'runtimes')) ||
    CONFIGURATION_FIELDS.some((field) => Object.hasOwn(row, field)) || Object.hasOwn(row, 'chipName'));
  const needsDateBasis = rows.some((row) => Object.hasOwn(row, 'dateBasis') || Object.hasOwn(row, 'quantBits'));
  const minor = (version) => Number(String(version).split('.')[1]);
  const wanted = needsDateBasis ? DATE_BASIS_CONTRACT_VERSION : needsRuntimes ? RUNTIMES_CONTRACT_VERSION : CONTRACT_VERSION;
  return minor(floor) > minor(wanted) ? floor : wanted;
}

const RUNTIME_KEYS = Object.freeze(['llamaCpp', 'llamaRn', 'litertLm', 'litertNpuRuntimeQualcomm', 'qairt', 'genie']);
/** runtimeVersions keys each engine may report (mirrors ENGINE_RUNTIMES in scripts/raw_session.py). */
const ENGINE_RUNTIMES = Object.freeze({
  'llama.cpp': ['llamaCpp', 'llamaRn'], 'litert-lm': ['litertLm', 'litertNpuRuntimeQualcomm', 'qairt'], 'genie-x': ['genie'],
});
/** LiteRT-LM runtimes that take part only in NPU runs. */
const NPU_ONLY_RUNTIMES = Object.freeze(['litertNpuRuntimeQualcomm', 'qairt']);

/**
 * Contract `build` (engineVersion, bindingVersion, runtimes) from a session's
 * `runtimeVersions` (raw schema; written by cpu-reference-v3 and accelerator-v2
 * apps). Only reported components appear; nothing is invented. Returns undefined
 * when the session carries no runtimeVersions; a malformed one is an error.
 */
export function buildFromSession(rec, where = '$') {
  if (!Object.hasOwn(rec, 'runtimeVersions') || rec.runtimeVersions === null) return undefined;
  const versions = rec.runtimeVersions;
  const fail = (message) => {
    throw new PublicationError(`${where}: runtimeVersions${message.startsWith('.') ? '' : ' '}${message}`);
  };
  if (!isObject(versions)) fail('must be an object');
  for (const key of Object.keys(versions)) if (!RUNTIME_KEYS.includes(key)) fail(`has unknown key ${key}`);
  // Never publish a runtime the row's engine (and backend) does not run on.
  const reported = RUNTIME_KEYS.filter((key) => versions[key] !== undefined && versions[key] !== null);
  const stray = reported.filter((key) => !(ENGINE_RUNTIMES[rec.engine] ?? []).includes(key) ||
    (rec.engine === 'litert-lm' && rec.backend !== 'npu' && NPU_ONLY_RUNTIMES.includes(key)));
  if (stray.length) fail(`names runtimes ${rec.engine} ${rec.backend ?? ''} does not run on: ${stray.join(', ')}`);
  const version = (key) => {
    const value = versions[key];
    if (value === undefined || value === null) return null;
    if (typeof value !== 'string' || !VERSION.test(value)) fail(`.${key} is not a version`);
    return value;
  };
  let llamaCpp = null;
  if (versions.llamaCpp !== undefined && versions.llamaCpp !== null) {
    const { buildNumber, commit } = isObject(versions.llamaCpp) ? versions.llamaCpp : fail('.llamaCpp must be an object');
    if (!Number.isInteger(buildNumber) || buildNumber < 1 || buildNumber > 9999999) {
      fail('.llamaCpp.buildNumber must be an integer build number');
    }
    if (typeof commit !== 'string' || !HEX_COMMIT.test(commit)) fail('.llamaCpp.commit must be 7-40 lowercase hex');
    llamaCpp = { buildNumber, commit };
  }
  const component = {
    'llama.cpp': llamaCpp && `b${llamaCpp.buildNumber}`, 'llama.rn': version('llamaRn'),
    'LiteRT-LM': version('litertLm'), 'litert-npu-runtime-qualcomm': version('litertNpuRuntimeQualcomm'),
    QAIRT: version('qairt'), 'geniex-android': version('genie'),
  };
  const runtimes = RUNTIME_NAMES.filter((name) => component[name] !== null)
    .map((name) => ({ name, version: component[name] }));
  const engineVersion = rec.engine === 'llama.cpp'
    ? llamaCpp && `llama.cpp b${llamaCpp.buildNumber} (${llamaCpp.commit.slice(0, 7)})`
    : rec.engine === 'litert-lm' ? component['LiteRT-LM'] && `LiteRT-LM ${component['LiteRT-LM']}`
      : rec.engine === 'genie-x' ? component['geniex-android'] && `geniex-android ${component['geniex-android']}` : null;
  const bindingVersion = rec.engine === 'llama.cpp' && component['llama.rn'] ? `llama.rn ${component['llama.rn']}` : null;
  const build = {
    ...(engineVersion ? { engineVersion } : {}),
    ...(bindingVersion ? { bindingVersion } : {}),
    ...(runtimes.length ? { runtimes } : {}),
  };
  return Object.keys(build).length ? build : undefined;
}

export function assertFindings(envelope, schema, label) {
  assertFinite(envelope, label);
  assertSchema(envelope, schema, label);
  const seen = new Set();
  for (const finding of envelope.findings) {
    if (seen.has(finding.id)) throw new PublicationError(`${label}: duplicate finding id ${finding.id}`);
    seen.add(finding.id);
  }
}
