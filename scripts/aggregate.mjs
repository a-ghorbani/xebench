#!/usr/bin/env node
/**
 * aggregate.mjs — convert raw xebench JSONL (one EngineSessionRecord per line,
 * pulled off-device) into EngineBenchmarkRow[] matching
 * pocketpal-website/lib/engines/types.ts, ready to drop into
 * lib/data/engine-benchmarks.json as `measured` rows.
 *
 * Usage:
 *   node scripts/aggregate.mjs results/raw-<runId>.jsonl [more.jsonl ...] > rows.json
 *   node scripts/aggregate.mjs results/*.jsonl --pretty > rows.json
 *
 * It does NOT invent numbers: every field comes from the on-device record.
 * Device marketing names are left as "<manufacturer> <model>"; hand-edit to the
 * website's canonical label if needed (e.g. "POCO F7 Ultra").
 */
import { readFileSync, writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const pretty = args.includes('--pretty');
const update = args.includes('--update'); // upsert into the canonical data/benchmarks.json
const files = args.filter((a) => !a.startsWith('--'));
if (files.length === 0) {
  console.error(
    'usage:\n' +
      '  node scripts/aggregate.mjs <raw.jsonl> [...] --update   # upsert measured rows into data/benchmarks.json (the canonical, published file)\n' +
      '  node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]  # print EngineBenchmarkRow[] to stdout',
  );
  process.exit(2);
}

const SOURCE_NAME = 'PocketPal R&D lab (xebench)';
const SOURCE_URL = 'https://github.com/a-ghorbani/xebench/blob/main/METHODOLOGY.md';

const round1 = (n) => (n == null ? null : Math.round(n * 10) / 10);

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
const friendlyDevice = (mfr, model) => {
  if (DEVICE_NAMES[model]) return DEVICE_NAMES[model];
  return [mfr, model].filter(Boolean).join(' ').trim() || 'unknown';
};

/** Compose a human note capturing the load-bearing methodology caveats. */
function asStated(rec) {
  const parts = [];
  const isLlamaBench = /llama-bench/.test(rec.protocol?.coldDefinition || '');
  parts.push(`n=${rec.protocol.nColdRuns} ${isLlamaBench ? 'reps (llama-bench mean)' : 'cold-run median'}`);
  parts.push(`prompt ${rec.protocol.promptLabel}`);
  parts.push(`decode ${rec.protocol.maxDecodeTokens} tok`);
  // prefill provenance differs per engine — surface it, it's the fairness crux
  const pm = rec.coldRuns?.[0]?.prefillMethod;
  if (pm && pm !== 'engine-timings') {
    parts.push(`prefill ${pm} (no engine counter)`);
  }
  if (rec.sustained) {
    parts.push(`sustained ${rec.sustained.degradationPct?.toFixed(0)}% decode drop over ${Math.round(rec.sustained.durationSec)}s`);
  }
  if (!rec.guardsPassed) parts.push(`GUARDS FAILED: ${rec.guardNotes?.join('; ')}`);
  return parts.join('; ');
}

const rows = [];
for (const file of files) {
  const text = readFileSync(file, 'utf8');
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    let rec;
    try {
      rec = JSON.parse(t);
    } catch {
      console.error(`skip unparseable line in ${file}`);
      continue;
    }
    if (rec.schema !== 'xebench-raw') continue;

    const dev = rec.deviceInfo ?? {};
    rows.push({
      engine: rec.engine,
      backend: rec.backend,
      platform: rec.platform,
      device: friendlyDevice(dev.manufacturer, dev.model),
      soc: dev.soc || undefined,
      model: rec.model,
      quant: rec.quant || undefined,
      ppTps: round1(rec.summary?.prefillTps?.median),
      tgTps: round1(rec.summary?.decodeTps?.median),
      ttftMs: round1(rec.summary?.ttftMs?.median),
      // deltaPss = engine+model+KV footprint, the number a user cares about
      memoryMb: round1(rec.summary?.deltaPssMb?.median ?? rec.summary?.peakPssMb?.median),
      provenance: 'measured',
      sourceName: SOURCE_NAME,
      sourceUrl: SOURCE_URL,
      sourceDate: (rec.timestampIso ?? '').slice(0, 7) || undefined,
      sampleCount: rec.protocol?.nColdRuns,
      asStated: asStated(rec),
    });
  }
}

// Deterministic order: platform, device, model, engine
rows.sort(
  (a, b) =>
    a.platform.localeCompare(b.platform) ||
    a.device.localeCompare(b.device) ||
    a.model.localeCompare(b.model) ||
    a.engine.localeCompare(b.engine),
);

if (update) {
  // Upsert freshly-aggregated MEASURED rows into the canonical published file,
  // preserving everything already there (vendor citations, other devices' runs).
  // Flow: capture -> aggregate --update -> commit data/benchmarks.json. The
  // website loads that file dynamically, so new numbers need no website change.
  const DATA = new URL('../data/benchmarks.json', import.meta.url);
  let envelope;
  try {
    envelope = JSON.parse(readFileSync(DATA, 'utf8'));
  } catch {
    envelope = {
      schemaVersion: '1.0',
      generatedAt: '',
      source: 'https://github.com/a-ghorbani/xebench',
      description:
        'Cross-engine on-device LLM benchmark results. provenance: measured = xebench lab runs; vendor = engine-maker official published numbers.',
      rowCount: 0,
      rows: [],
    };
  }
  const key = (r) => [r.engine, r.backend, r.platform, r.device, r.model, r.quant, r.provenance].join('|');
  const byKey = new Map(envelope.rows.map((r) => [key(r), r]));
  let added = 0;
  let changed = 0;
  for (const r of rows) {
    if (byKey.has(key(r))) changed++;
    else added++;
    byKey.set(key(r), r);
  }
  const merged = [...byKey.values()].sort(
    (a, b) =>
      a.platform.localeCompare(b.platform) ||
      a.device.localeCompare(b.device) ||
      a.model.localeCompare(b.model) ||
      a.engine.localeCompare(b.engine),
  );
  envelope.generatedAt = new Date().toISOString().slice(0, 10);
  envelope.rowCount = merged.length;
  envelope.rows = merged;
  writeFileSync(DATA, JSON.stringify(envelope, null, 2) + '\n');
  console.error(
    `merged ${rows.length} measured rows into data/benchmarks.json (+${added} new, ${changed} updated; ${merged.length} total). Commit + push to publish.`,
  );
} else {
  process.stdout.write(JSON.stringify(rows, null, pretty ? 2 : 0) + '\n');
  console.error(`aggregated ${rows.length} measured rows from ${files.length} file(s)`);
}
