/** React Native provides performance.now(); its TS globals omit this Web API. */
export const monotonicNow = () =>
  (globalThis as unknown as {performance: {now(): number}}).performance.now();
