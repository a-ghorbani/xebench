#!/usr/bin/env node
/**
 * Pins the native ExecuTorch libraries that react-native-executorch downloads at install.
 *
 * react-native-executorch's postinstall (node_modules/react-native-executorch/scripts/
 * download-libs.js) fetches prebuilt archives from its GitHub release
 * v<nativeLibsVersion>-libs and checks them against .sha256 files fetched from the same
 * release, so a re-cut release would change the build without any change here. This
 * script runs after it (the app's postinstall) and fails the install unless the extracted
 * files are exactly the ones in executorch-libs.lock.json:
 *
 *   - the package and native-libs versions;
 *   - the build configuration the download wrote (XNNPACK only: the "react-native-executorch"
 *     block in package.json); no other backend library is present;
 *   - the SHA-256 of every Android arm64-v8a library and of executorch.jar;
 *   - a digest of the headers (third-party/include), which are compiled into
 *     libreact-native-executorch.so, and the ExecuTorch version they declare.
 *
 * After an intended bump: npm install, review, then `node scripts/check-executorch-libs.js --write`
 * and commit the lock.
 */
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const APP = path.resolve(__dirname, '..');
const LOCK = path.join(APP, 'executorch-libs.lock.json');
const PACKAGE = path.join(APP, 'node_modules', 'react-native-executorch');
const THIRD_PARTY = path.join(PACKAGE, 'third-party');
const ANDROID_LIBS = path.join(THIRD_PARTY, 'android', 'libs');
const INCLUDE = path.join(THIRD_PARTY, 'include');
const VERSION_HEADER = path.join(INCLUDE, 'executorch', 'runtime', 'core', 'version.h');
// The ABIs the app builds (android/gradle.properties reactNativeArchitectures).
const ABIS = ['arm64-v8a'];

const sha256 = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const relative = (file) => path.relative(PACKAGE, file).split(path.sep).join('/');

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.isFile()) out.push(full);
    else throw new Error(`${relative(full)}: not a regular file or directory`);
  }
  return out;
}

/** SHA-256 over "<path>\0<sha256>\n" for every header file, sorted by path. */
function treeDigest(dir) {
  const lines = walk(dir).map((file) => `${relative(file)}\0${sha256(file)}\n`).sort();
  return { files: lines.length, sha256: crypto.createHash('sha256').update(lines.join('')).digest('hex') };
}

function executorchVersion() {
  const match = /#define ET_VERSION "([0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*)"/.exec(
    fs.readFileSync(VERSION_HEADER, 'utf8'));
  if (!match) throw new Error(`${relative(VERSION_HEADER)}: no ET_VERSION`);
  return match[1];
}

function observe() {
  const pkg = JSON.parse(fs.readFileSync(path.join(PACKAGE, 'package.json'), 'utf8'));
  const libraries = {};
  for (const abi of ABIS) {
    const dir = path.join(ANDROID_LIBS, 'executorch', abi);
    for (const name of fs.readdirSync(dir).sort()) libraries[relative(path.join(dir, name))] = sha256(path.join(dir, name));
  }
  const jar = path.join(ANDROID_LIBS, 'executorch.jar');
  return {
    package: { name: pkg.name, version: pkg.version, nativeLibsVersion: pkg.nativeLibsVersion },
    executorch: executorchVersion(),
    buildConfig: JSON.parse(fs.readFileSync(path.join(PACKAGE, 'rne-build-config.json'), 'utf8')),
    files: { ...libraries, [relative(jar)]: sha256(jar) },
    headers: treeDigest(INCLUDE),
  };
}

function differences(expected, actual, where = '') {
  if (JSON.stringify(expected) === JSON.stringify(actual)) return [];
  if (expected && actual && typeof expected === 'object' && typeof actual === 'object') {
    const keys = [...new Set([...Object.keys(expected), ...Object.keys(actual)])].sort();
    return keys.flatMap((key) => differences(expected[key], actual[key], `${where}/${key}`));
  }
  return [`${where || '/'}: expected ${JSON.stringify(expected)}, found ${JSON.stringify(actual)}`];
}

function main() {
  const actual = observe();
  if (process.argv.includes('--write')) {
    fs.writeFileSync(LOCK, JSON.stringify(actual, null, 2) + '\n');
    console.log(`[xebench] wrote ${path.relative(APP, LOCK)}`);
    return;
  }
  const { $comment, ...expected } = JSON.parse(fs.readFileSync(LOCK, 'utf8'));
  const problems = differences(expected, actual);
  if (problems.length) {
    console.error('[xebench] react-native-executorch native libraries differ from executorch-libs.lock.json:');
    for (const problem of problems) console.error(`  ${problem}`);
    console.error('  Reinstall (npm ci) or, after an intended bump, review and run: node scripts/check-executorch-libs.js --write');
    process.exit(1);
  }
  console.log(`[xebench] ExecuTorch ${actual.executorch} native libraries match executorch-libs.lock.json ` +
    `(react-native-executorch ${actual.package.version}, libs v${actual.package.nativeLibsVersion}, XNNPACK only)`);
}

try {
  main();
} catch (error) {
  console.error(`[xebench] cannot verify react-native-executorch native libraries: ${error.message}`);
  console.error('  They are downloaded by its postinstall; run npm ci with install scripts enabled.');
  process.exit(1);
}
