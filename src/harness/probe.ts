import { NativeModules } from 'react-native';
import type { DeviceInfo, MemorySnapshot, ThermalSnapshot } from './types';

const { BenchProbe } = NativeModules;

if (!BenchProbe) {
  throw new Error(
    'BenchProbe native module not linked. Did scripts/setup-app.sh copy the Kotlin files and register BenchProbePackage in MainApplication?',
  );
}

export const probe = {
  getMemorySnapshot: (): Promise<MemorySnapshot> => BenchProbe.getMemorySnapshot(),
  getThermalSnapshot: (): Promise<ThermalSnapshot> => BenchProbe.getThermalSnapshot(),
  getDeviceInfo: (): Promise<DeviceInfo> => BenchProbe.getDeviceInfo(),
  appendResultLine: (fileName: string, line: string): Promise<string> =>
    BenchProbe.appendResultLine(fileName, line),
  setKeepScreenOn: (on: boolean): Promise<void> => BenchProbe.setKeepScreenOn(on),
  requestGc: (): Promise<void> => BenchProbe.requestGc(),
  getFileSizeMb: (path: string): Promise<number> => BenchProbe.getFileSizeMb(path),
};

/** Polls PSS at `intervalMs` and tracks the max seen. */
export class MemorySampler {
  private timer: ReturnType<typeof setInterval> | null = null;
  private peakPssMb = 0;

  async start(intervalMs = 500): Promise<void> {
    const first = await probe.getMemorySnapshot();
    this.peakPssMb = first.totalPssMb;
    this.timer = setInterval(async () => {
      try {
        const s = await probe.getMemorySnapshot();
        if (s.totalPssMb > this.peakPssMb) this.peakPssMb = s.totalPssMb;
      } catch {
        // sampling is best-effort
      }
    }, intervalMs);
  }

  async stop(): Promise<{ peakPssMb: number; vmHwmMb: number }> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const last = await probe.getMemorySnapshot();
    if (last.totalPssMb > this.peakPssMb) this.peakPssMb = last.totalPssMb;
    return { peakPssMb: this.peakPssMb, vmHwmMb: last.vmHwmMb };
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
