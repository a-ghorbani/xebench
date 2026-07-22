import type { Aggregate } from './types';

export function median(values: number[]): number {
  if (values.length === 0) return NaN;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
}

/** Interquartile range via linear-interpolated quartiles (type-7, matches numpy default). */
export function iqr(values: number[]): number {
  if (values.length < 2) return 0;
  const s = [...values].sort((a, b) => a - b);
  const q = (p: number) => {
    const idx = p * (s.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    return s[lo] + (s[hi] - s[lo]) * (idx - lo);
  };
  return q(0.75) - q(0.25);
}

export function aggregate(values: number[]): Aggregate {
  return {
    median: round1(median(values)),
    iqr: round1(iqr(values)),
    min: round1(Math.min(...values)),
    max: round1(Math.max(...values)),
    n: values.length,
  };
}

export function round1(x: number): number {
  return Math.round(x * 10) / 10;
}
