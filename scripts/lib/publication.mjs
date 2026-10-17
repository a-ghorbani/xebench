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
  'pattern', 'minLength', 'minItems', 'allOf', 'if', 'then', 'else',
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
const jsonEqual = (a, b) => {
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

const isUri = (value) => {
  try {
    return /^[a-z][a-z0-9+.-]*:/i.test(value) && Boolean(new URL(value));
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
  for (const key of ['minLength', 'minItems']) {
    if (schema[key] !== undefined && !(Number.isInteger(schema[key]) && schema[key] >= 0)) {
      throw new PublicationError(`${key} at ${path} must be a non-negative integer`);
    }
  }
  if (schema.pattern !== undefined) compilePattern(schema.pattern, path);
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
    if (schema.pattern !== undefined && !compilePattern(schema.pattern, path).test(value)) {
      errors.push(`${path}: ${JSON.stringify(value)} does not match ${schema.pattern}`);
    }
  }
  if (Array.isArray(value) && schema.minItems !== undefined && value.length < schema.minItems) {
    errors.push(`${path}: fewer than ${schema.minItems} items`);
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

/** Row fields introduced in 1.1; valid on provenance=measured rows only. */
export const MEASURED_ONLY_FIELDS = Object.freeze([
  'validation', 'guardsPassed', 'conditionPolicy', 'protocolVersion', 'timingMethod', 'dispersion', 'evidence', 'build',
]);

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
    for (const field of MEASURED_ONLY_FIELDS) {
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
  if (row.validation === 'legacy') {
    for (const field of ['dispersion', 'evidence']) {
      if (Object.hasOwn(row, field)) errors.push(`${path}: legacy rows carry no ${field}`);
    }
  }
  for (const [metric, spread] of Object.entries(isObject(row.dispersion) ? row.dispersion : {})) {
    if (isObject(spread) && typeof spread.min === 'number' && typeof spread.max === 'number' && spread.min > spread.max) {
      errors.push(`${path}.dispersion.${metric}: min ${spread.min} > max ${spread.max}`);
    }
  }
  const sessions = row.evidence?.sessionIds;
  if (Array.isArray(sessions) && new Set(sessions).size !== sessions.length) {
    errors.push(`${path}.evidence.sessionIds: duplicate session id`);
  }
  return errors;
}

/** Schema plus envelope and row invariants the schema cannot express. */
export function assertBenchmarks(envelope, schema, label) {
  assertFinite(envelope, label);
  // Contract invariants are reported with the schema errors: the schema rejects
  // most of them too, but these messages say which invariant was broken.
  const invariants = Array.isArray(envelope?.rows)
    ? envelope.rows.flatMap((row, index) => (isObject(row) ? rowInvariantErrors(row, `$.rows[${index}]`) : []))
    : [];
  assertSchema(envelope, schema, label, schema, invariants);
  if (envelope.rowCount !== undefined && envelope.rowCount !== envelope.rows.length) {
    throw new PublicationError(`${label}: rowCount ${envelope.rowCount} != ${envelope.rows.length} rows`);
  }
  if (String(envelope.schemaVersion).split('.')[0] !== '1') {
    throw new PublicationError(`${label}: unsupported schemaVersion ${envelope.schemaVersion}`);
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
  if (envelope.schemaVersion !== CONTRACT_VERSION) needs.push(`schemaVersion is ${envelope.schemaVersion}, not ${CONTRACT_VERSION}`);
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
  if (envelope.schemaVersion !== '1.0' && envelope.schemaVersion !== CONTRACT_VERSION) {
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

/**
 * Write next to the target, flush, then rename over it: readers see old or new,
 * never partial. Refuses if the target no longer holds `expectedBefore`.
 */
export function writeAtomic(target, text, expectedBefore) {
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
    if (readFileSync(target, 'utf8') !== expectedBefore) {
      throw new PublicationError(`${basename(target)} changed while it was being regenerated; re-run`);
    }
    renameSync(tmp, target);
    try {
      const dir = openSync(dirname(target), 'r');
      fsyncSync(dir); // persist the rename; best-effort where directories cannot be fsynced
      closeSync(dir);
    } catch {
      // not supported on this platform
    }
  } catch (error) {
    try {
      if (fd !== undefined) closeSync(fd);
    } catch {
      // keep the original error; still remove the temp file
    }
    try {
      unlinkSync(tmp);
    } catch {
      // the temp file was never created or was already renamed
    }
    throw error;
  }
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
