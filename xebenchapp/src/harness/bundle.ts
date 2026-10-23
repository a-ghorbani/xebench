/**
 * Artifact identity of a GENIE-X QAIRT bundle, which is a directory (genie_config.json, the QNN
 * context binaries, the tokenizer) rather than one model file.
 *
 * The bundle digest is the SHA-256 of the bundle's `sha256sum` listing: one line
 * "<sha256 of the file>  <file name>\n" per regular file directly in the directory, sorted by name
 * (byte order). `ls | LC_ALL=C sort | xargs sha256sum -- | sha256sum` in the bundle directory gives
 * the same value, and
 * scripts/stage_geniex_bundle.py records it next to the pinned SHA-256 of the Qualcomm zip, so a
 * configuration pins the bundle with modelSha256 exactly as it pins a GGUF. sizeBytes is the sum of
 * the file sizes. A subdirectory or an unusual file name refuses the bundle.
 */
export interface BundleFs {
  readDir(path: string): Promise<{name: string; path: string; size: number; isFile(): boolean}[]>;
  hash(path: string, algorithm: 'sha256'): Promise<string>;
  writeFile(path: string, contents: string, encoding: 'utf8'): Promise<void>;
  unlink(path: string): Promise<void>;
}

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/** The listing the digest hashes; entries are {name, sha256}. */
export function bundleListing(entries: {name: string; sha256: string}[]): string {
  return [...entries].sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
    .map(entry => `${entry.sha256}  ${entry.name}\n`).join('');
}

/** {sha256, sizeBytes} of the bundle directory; scratch is a writable file path for the listing. */
export async function bundleArtifact(fs: BundleFs, directory: string, scratch: string):
  Promise<{sha256: string; sizeBytes: number}> {
  const items = await fs.readDir(directory);
  if (!items.length) {
    throw new Error('The QAIRT bundle directory is empty');
  }
  const entries: {name: string; sha256: string}[] = [];
  let sizeBytes = 0;
  for (const item of items) {
    if (!item.isFile() || !NAME.test(item.name)) {
      throw new Error('A QAIRT bundle holds regular files with plain names only');
    }
    entries.push({name: item.name, sha256: await fs.hash(item.path, 'sha256')});
    sizeBytes += item.size;
  }
  await fs.writeFile(scratch, bundleListing(entries), 'utf8');
  try {
    return {sha256: await fs.hash(scratch, 'sha256'), sizeBytes};
  } finally {
    await fs.unlink(scratch);
  }
}
