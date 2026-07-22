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
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const pretty = args.includes('--pretty');
const files = args.filter((a) => !a.startsWith('--'));
if (files.length === 0) {
  console.error('usage: node scripts/aggregate.mjs <raw.jsonl> [...] [--pretty]');
  process.exit(2);
}

const SOURCE_NAME = 'PocketPal R&D lab (xebench)';
const SOURCE_URL =
  'https://github.com/a-ghorbani/pocketpal-ai/tree/main/evaluation/xebench#methodology';

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

process.stdout.write(JSON.stringify(rows, null, pretty ? 2 : 0) + '\n');
console.error(`aggregated ${rows.length} measured rows from ${files.length} file(s)`);
