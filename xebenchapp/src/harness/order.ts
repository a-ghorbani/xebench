/** Run-order randomization with a recorded seed, so any order can be reproduced. */
/* eslint-disable no-bitwise -- seeded PRNG arithmetic */
export const ORDER_METHOD = 'mulberry32-fisher-yates-v1' as const;

export interface RunOrder {
  method: typeof ORDER_METHOD;
  seed: number;
  seedSource: 'configured' | 'generated';
  /** Run IDs in executed order. */
  order: string[];
  /** Zero-based position of this session's run in `order`. */
  position: number;
}

function mulberry32(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/** Fisher-Yates shuffle driven by mulberry32(seed); identical seeds give identical orders. */
export function seededOrder<T>(items: readonly T[], seed: number): T[] {
  const random = mulberry32(seed);
  const result = [...items];
  for (let index = result.length - 1; index > 0; index--) {
    const swap = Math.floor(random() * (index + 1));
    [result[index], result[swap]] = [result[swap], result[index]];
  }
  return result;
}

/** Not cryptographic: the seed only needs to vary between invocations and be recorded. */
export const generateSeed = () => Math.floor(Math.random() * 0x100000000) >>> 0;
