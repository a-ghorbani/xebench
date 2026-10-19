/**
 * The rows measured before the app recorded engine versions (July 2026), frozen: each key with
 * its published numbers, its month (sourceDate) and what the month rests on (dateBasis,
 * contract 1.3). scripts/date_legacy_rows.mjs writes these dates; scripts/lib/sets.mjs displays a
 * row without versions only when it is one of these rows, unchanged. No row joins this list: a
 * new measurement is published from an evidence bundle, with its versions.
 *
 * Keys use the board's label vocabulary (LiteRT-LM "Q8", upper-case SoC ids);
 * LEGACY_LABELS maps the labels the July rows were first published with.
 *
 *   measured         the month comes from the run's own record: the raw record pulled off the
 *                    phone (rd-team evaluation/prototypes/llm-cross-engine-bench/results/,
 *                    files written 2026-07-17 to 2026-07-21, matching the row's numbers), the
 *                    dated run reports there (MORNING-REPORT.md, 2026-07-18, addendum
 *                    2026-07-21), or a record timestamp.
 *   first-published  no record with the row's numbers survives; the month is that of the first
 *                    commit carrying the row (f309080, 2026-07-22).
 */
import { rowKey } from './publication.mjs';

/** Labels the July rows were first published with -> the board vocabulary (same meaning). */
export const LEGACY_LABELS = Object.freeze({
  quant: Object.freeze({ 'litert-lm': Object.freeze({ q8: 'Q8' }) }),
  soc: Object.freeze({ sdm845: 'SDM845' }),
});

/** The row with its labels in the board vocabulary; every other field is the same. */
export function withBoardLabels(row) {
  const quant = LEGACY_LABELS.quant[row.engine]?.[row.quant];
  const soc = LEGACY_LABELS.soc[row.soc];
  return { ...row, ...(quant ? { quant } : {}), ...(soc ? { soc } : {}) };
}

const M = 'measured';
const P = 'first-published';
/** key -> [ppTps, tgTps, sourceDate, dateBasis, basis]; the basis is documentation only. */
export const LEGACY_DATES = Object.freeze({
  'executorch|cpu|android|Google Pixel 9|Llama 3.2 1B|SpinQuant 4-bit|measured': [171.7, 15.9, '2026-07', M, 'raw record, Pixel 9, 2026-07-17/21'],
  'llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_K_M|measured': [144, 24.1, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_0|measured': [211.2, 25.9, '2026-07', M, 'raw record, Pixel 9, 2026-07-21'],
  'litert-lm|cpu|android|Google Pixel 9|Qwen2.5 1.5B|Q8|measured': [77.3, 8.2, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'llama.cpp|cpu|android|Google Pixel 9|Qwen2.5 1.5B|Q8_0|measured': [135.7, 11.7, '2026-07', M, 'raw record, Pixel 9, 2026-07-17'],
  'executorch|cpu|android|OnePlus 6|Llama 3.2 1B|SpinQuant 4-bit|measured': [11.4, 4.8, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'llama.cpp|cpu|android|OnePlus 6|Llama 3.2 1B|Q4_K_M|measured': [16.1, 8.6, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'llama.cpp|cpu|android|OnePlus 6|Qwen2.5 1.5B|Q8_0|measured': [4.9, 3.2, '2026-07', M, 'raw record, OnePlus 6, 2026-07-18'],
  'litert-lm|cpu|android|POCO F8 Ultra|Gemma 3 1B|int4|measured': [691.7, 40.6, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Gemma 3 1B|Q4_0 (QAT)|measured': [382.2, 42.7, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'executorch|cpu|android|POCO F8 Ultra|Llama 3.2 1B|SpinQuant 4-bit|measured': [311.9, 34.3, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'genie-x|npu|android|POCO F8 Ultra|Llama 3.2 1B|W4A16|measured': [354.6, 16.8, '2026-07', M, 'run report addendum and ENGINES.md, measured 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Llama 3.2 1B|Q4_0|measured': [300.3, 47.3, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Llama 3.2 1B|Q4_K_M|measured': [206.9, 43.4, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'litert-lm|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8|measured': [323.8, 24.2, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured': [236.3, 27.9, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'llama.cpp|gpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured': [236.9, 31.6, '2026-07', M, 'raw record, F8 Ultra, 2026-07-21'],
  'litert-lm|cpu|android|Samsung Galaxy S23|Gemma 3 1B|int4|measured': [186.6, 36.1, '2026-07', P, 'no record with these numbers; first published in f309080, 2026-07-22'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Gemma 3 1B|Q4_0 (QAT)|measured': [236.1, 21.9, '2026-07', P, 'no record with these numbers; first published in f309080, 2026-07-22'],
  'executorch|cpu|android|Samsung Galaxy S23|Llama 3.2 1B|SpinQuant 4-bit|measured': [149.9, 21.1, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18/21'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Llama 3.2 1B|Q4_K_M|measured': [124, 25.9, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'genie-x|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [87.9, 12.3, '2026-07', M, 'raw record timestampIso 2026-07-18T02:35:00Z'],
  'litert-lm|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8|measured': [78.6, 16, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'llama.cpp|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [105.2, 19, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
  'llama.cpp|gpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured': [97.6, 18.4, '2026-07', M, 'raw record, Galaxy S23, 2026-07-18'],
});

/** Why `row` is not one of the frozen rows without versions, exactly as dated; null when it is. */
export function legacyDateMismatch(row) {
  const entry = LEGACY_DATES[rowKey(row)];
  if (!entry) return 'it is not one of the July 2026 rows without versions (scripts/lib/legacy-dates.mjs)';
  const [ppTps, tgTps, sourceDate, dateBasis] = entry;
  if (row.ppTps !== ppTps || row.tgTps !== tgTps) return `its numbers ${row.ppTps}/${row.tgTps} are not the dated ${ppTps}/${tgTps}`;
  if (row.sourceDate !== sourceDate || row.dateBasis !== dateBasis) {
    return `it does not carry its month (sourceDate ${sourceDate}, dateBasis ${dateBasis}); run node scripts/date_legacy_rows.mjs`;
  }
  return null;
}
