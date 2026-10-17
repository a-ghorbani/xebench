import {NativeModules} from 'react-native';

/**
 * Identity of the installed binary, embedded at build time (android/app/build.gradle
 * BuildConfig fields) and read through the BuildInfo native module (src/main, all flavors).
 * Anything the module does not report, or reports in an unexpected form, stays null.
 */
export interface BuildIdentity {
  /** 'unavailable' when the native module is missing; every other field is then null. */
  status: 'reported' | 'unavailable';
  /** Commit the APK was built from; null when the build ran outside a git checkout. */
  gitCommit: string | null;
  /** Tracked or untracked, non-ignored changes in the checkout at build time. */
  gitDirty: boolean | null;
  flavor: 'reference' | 'experimental' | 'geniex' | null;
  buildType: 'release' | 'debug' | null;
  versionName: string | null;
  versionCode: number | null;
  /** SHA-256 of each certificate that signed the APK contents (lowercase hex). */
  signingCertSha256: string[] | null;
}

/** BuildConfig values that are not part of the binary's identity record. */
export interface BuildInfo {
  identity: BuildIdentity;
  /** SHA-256 of configs/cpu-reference-v1.json as bundled into this build. */
  bundledConfigSha256: string | null;
}

const HEX40 = /^[0-9a-f]{40}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const VERSION_NAME = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;
const FLAVORS = ['reference', 'experimental', 'geniex'] as const;
const BUILD_TYPES = ['release', 'debug'] as const;

export const UNAVAILABLE_BUILD: BuildIdentity = {status: 'unavailable', gitCommit: null, gitDirty: null,
  flavor: null, buildType: null, versionName: null, versionCode: null, signingCertSha256: null};

const pick = <T extends string>(value: unknown, options: readonly T[]): T | null =>
  options.includes(value as T) ? value as T : null;
const match = (value: unknown, pattern: RegExp): string | null =>
  typeof value === 'string' && pattern.test(value) ? value : null;

/** Strict normalization of the native payload; exported for tests. */
export function normalizeBuildInfo(raw: unknown): BuildInfo {
  if (!raw || typeof raw !== 'object') {
    return {identity: UNAVAILABLE_BUILD, bundledConfigSha256: null};
  }
  const value = raw as Record<string, unknown>;
  const certs = Array.isArray(value.signingCertSha256) && value.signingCertSha256.length > 0 &&
    value.signingCertSha256.length <= 8 && value.signingCertSha256.every(item => match(item, HEX64))
    ? [...value.signingCertSha256] as string[] : null;
  const versionCode = typeof value.versionCode === 'number' && Number.isInteger(value.versionCode) &&
    value.versionCode >= 0 && value.versionCode <= 2100000000 ? value.versionCode : null;
  return {
    identity: {
      status: 'reported',
      gitCommit: match(value.gitCommit, HEX40),
      gitDirty: typeof value.gitDirty === 'boolean' ? value.gitDirty : null,
      flavor: pick(value.flavor, FLAVORS),
      buildType: pick(value.buildType, BUILD_TYPES),
      versionName: match(value.versionName, VERSION_NAME),
      versionCode,
      signingCertSha256: certs,
    },
    bundledConfigSha256: match(value.bundledConfigSha256, HEX64),
  };
}

export async function readBuildInfo(): Promise<BuildInfo> {
  const native = NativeModules.BuildInfo as {identity?: () => Promise<unknown>} | undefined;
  if (!native?.identity) {
    return normalizeBuildInfo(null);
  }
  try {
    return normalizeBuildInfo(await native.identity());
  } catch {
    return normalizeBuildInfo(null);
  }
}
