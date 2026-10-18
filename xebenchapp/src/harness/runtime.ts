/**
 * Versions of the inference runtimes a session ran on, taken from the shipped artifacts:
 * llama.rn's exported BuildInfo (the vendored llama.cpp build number and commit) and its
 * package version, and the LiteRT-LM / Qualcomm NPU runtime / QAIRT / GENIE-X versions that
 * the engine bridges report from the APK (Gradle-resolved AARs, the QAIRT build string in
 * libQnnHtp.so). A component the session does not use, or that reports nothing usable, is
 * null; nothing is typed by hand.
 *
 * Mirrored by scripts/raw_session.py and scripts/export_evidence.py; keep in step.
 */
export interface RuntimeVersions {
  /** llama.cpp as vendored by llama.rn: build number (b<number>) and commit. */
  llamaCpp: {buildNumber: number; commit: string} | null;
  llamaRn: string | null;
  litertLm: string | null;
  litertNpuRuntimeQualcomm: string | null;
  qairt: string | null;
  genie: string | null;
}

export const NO_RUNTIME_VERSIONS: RuntimeVersions = {llamaCpp: null, llamaRn: null, litertLm: null,
  litertNpuRuntimeQualcomm: null, qairt: null, genie: null};

/** Short version strings only: digits/letters with . + - separators, no spaces or free text. */
export const VERSION = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$/;
const COMMIT = /^[0-9a-f]{7,40}$/;

export const version = (value: unknown): string | null =>
  typeof value === 'string' && VERSION.test(value) ? value : null;

/** llama.rn's BuildInfo ({number: '11385', commit: 'bf9a0cc'}); null unless both are well formed. */
export function llamaCppBuild(info: unknown): RuntimeVersions['llamaCpp'] {
  const value = (info ?? {}) as {number?: unknown; commit?: unknown};
  const number = typeof value.number === 'number' ? value.number :
    typeof value.number === 'string' && /^[0-9]{1,9}$/.test(value.number) ? Number(value.number) : NaN;
  const commit = typeof value.commit === 'string' && COMMIT.test(value.commit) ? value.commit : null;
  return Number.isInteger(number) && number > 0 && commit !== null ? {buildNumber: number, commit} : null;
}
