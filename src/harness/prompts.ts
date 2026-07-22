/**
 * Standard prompts, pre-formatted with the Llama 3.2 chat template.
 *
 * CRITICAL for apples-to-apples: both engines receive the SAME raw string —
 * llama.rn via completion({ prompt }) and react-native-executorch via forward().
 * Neither engine applies its own chat template on top, so tokenizer input is
 * byte-identical. Both stacks use the same Llama 3.2 tokenizer, so prompt
 * token counts should match; the harness records each engine's own count and
 * flags a mismatch.
 *
 * Prompt lengths are calibrated in *text*, not tokens (no synthetic-token API
 * exists cross-engine). The standard prompt lands around ~512 Llama-3 tokens;
 * exact counts are recorded per run and used in all per-token math, so the
 * comparison does not depend on hitting a target token count.
 */

const LLAMA32_HEADER =
  '<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n' +
  'You are a concise assistant.<|eot_id|>' +
  '<|start_header_id|>user<|end_header_id|>\n\n';

const LLAMA32_FOOTER = '<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n';

// ~430 words of neutral, public-domain-style prose -> ~512 Llama-3 tokens with template.
const LONG_BODY =
  'Summarize the following text in three sentences. ' +
  'The history of measurement is a history of standardization. Early units were ' +
  'anthropometric: the cubit was the length of a forearm, the foot the length of a foot, ' +
  'and the inch the width of a thumb. Such units were convenient but inconsistent, since ' +
  'no two forearms are alike, and commerce across regions demanded conversions that were ' +
  'contested and frequently revised. The Egyptians cut royal cubit rods from granite so ' +
  'that builders could check their working copies against a master, an early instance of ' +
  'traceability. Medieval markets relied on local standards kept in town halls, and a ' +
  'merchant traveling between cities carried conversion tables the way a modern traveler ' +
  'carries adapters. The scientific revolution sharpened the demand: instruments could ' +
  'now detect differences far smaller than the variation between regional standards, and ' +
  'natural philosophers began to argue that units should be derived from invariants of ' +
  'nature rather than from artifacts or anatomy. The French Revolution provided the ' +
  'political opening, and the metre was defined as one ten-millionth of the quarter ' +
  'meridian, surveyed at great expense between Dunkirk and Barcelona. The survey ' +
  'contained small errors, so the metre as realized differed slightly from its ideal ' +
  'definition, and the platinum bar deposited in the archives became the de facto truth. ' +
  'A century later the international prototype kilogram and metre bars were distributed ' +
  'to signatory nations, and metrology became a treaty obligation. The twentieth century ' +
  'replaced artifacts one by one: the second was tied to a hyperfine transition of ' +
  'cesium, the metre to the distance light travels in a defined fraction of a second, ' +
  'and finally in 2019 the kilogram was fixed by assigning an exact value to the Planck ' +
  'constant, retiring the last physical artifact. Each redefinition preserved continuity: ' +
  'the new definition was chosen so that the size of the unit did not measurably change, ' +
  'only its foundation. The result is a system in which any suitably equipped laboratory ' +
  'can realize the base units from first principles without reference to any object, and ' +
  'in which the uncertainty of realization, rather than the drift of an artifact, sets ' +
  'the limit of precision. Standardization did not merely serve science; it served trade, ' +
  'engineering, medicine, and law, because a measurement is a claim that others must be ' +
  'able to check.';

const SHORT_BODY = 'Write a short story about a lighthouse keeper who finds a message in a bottle.';

/** ~512-token prompt used for the cold-run protocol (prefill-meaningful). */
export const STANDARD_PROMPT = LLAMA32_HEADER + LONG_BODY + LLAMA32_FOOTER;
export const STANDARD_PROMPT_LABEL = 'std-pp512-v1';

/** Short prompt used for the sustained (decode-heavy) phase. */
export const SUSTAINED_PROMPT = LLAMA32_HEADER + SHORT_BODY + LLAMA32_FOOTER;
export const SUSTAINED_PROMPT_LABEL = 'sustained-short-v1';

/** Decode budget per run — comparable to llama-bench tg128 convention. */
export const MAX_DECODE_TOKENS = 128;
