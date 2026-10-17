/**
 * Shared, dependency-free validation for the published data files.
 *
 * `validateSchema` implements only the JSON Schema 2020-12 keywords that
 * data/*.schema.json use. Any other keyword is rejected so that a schema change
 * cannot silently bypass validation; extend this file when a schema needs more.
 */
import { readFileSync } from 'node:fs';

const ANNOTATIONS = new Set(['$schema', '$id', '$comment', '$defs', 'title', 'description', 'default', 'examples']);
const ASSERTIONS = new Set([
  'type', 'enum', 'required', 'properties', 'additionalProperties', 'items', '$ref', 'minimum', 'format',
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

/** Returns a list of "path: message" errors; empty when `value` conforms. */
export function validateSchema(value, schema, root = schema, path = '$', errors = []) {
  if (typeof schema === 'boolean') {
    if (!schema) errors.push(`${path}: not allowed`);
    return errors;
  }
  for (const keyword of Object.keys(schema)) {
    if (!ANNOTATIONS.has(keyword) && !ASSERTIONS.has(keyword)) {
      throw new PublicationError(`unsupported schema keyword ${keyword} at ${path}`);
    }
  }
  if (schema.$ref) validateSchema(value, resolveRef(root, schema.$ref), root, path, errors);
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => matchesType(value, type))) {
      errors.push(`${path}: expected ${types.join('|')}, got ${typeOf(value)}`);
      return errors;
    }
  }
  if (schema.enum && !schema.enum.some((item) => item === value)) {
    errors.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
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
      if (Object.hasOwn(properties, key)) validateSchema(item, properties[key], root, `${path}.${key}`, errors);
      else if (schema.additionalProperties !== undefined) {
        validateSchema(item, schema.additionalProperties, root, `${path}.${key}`, errors);
      }
    }
  }
  if (Array.isArray(value) && schema.items !== undefined) {
    value.forEach((item, index) => validateSchema(item, schema.items, root, `${path}[${index}]`, errors));
  }
  return errors;
}

export function assertSchema(value, schema, label, root = schema) {
  const errors = validateSchema(value, schema, root);
  if (errors.length) {
    const shown = errors.slice(0, 20).join('\n  ');
    const more = errors.length > 20 ? `\n  ... ${errors.length - 20} more` : '';
    throw new PublicationError(`${label} does not match its schema:\n  ${shown}${more}`);
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

/** Schema plus envelope invariants the schema cannot express. */
export function assertBenchmarks(envelope, schema, label) {
  assertFinite(envelope, label);
  assertSchema(envelope, schema, label);
  if (envelope.rowCount !== undefined && envelope.rowCount !== envelope.rows.length) {
    throw new PublicationError(`${label}: rowCount ${envelope.rowCount} != ${envelope.rows.length} rows`);
  }
  if (String(envelope.schemaVersion).split('.')[0] !== '1') {
    throw new PublicationError(`${label}: unsupported schemaVersion ${envelope.schemaVersion}`);
  }
  const seen = new Set();
  for (const row of envelope.rows) {
    const key = rowKey(row);
    if (seen.has(key)) throw new PublicationError(`${label}: duplicate row key ${key}`);
    seen.add(key);
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
