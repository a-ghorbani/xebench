/**
 * Observed-backend evidence (accelerator-v1). A requested backend is not proof of
 * where inference ran. Each repetition records what the engine or process reported
 * after loading, and a status derived from it by fixed rules:
 *
 *   confirmed   the engine itself reported executing on the requested backend
 *               (llama.rn: context `gpu` flag and used devices)
 *   consistent  indirect evidence only: creating this engine newly loaded the
 *               accelerator runtime into the process and the engine accepted the
 *               backend (LiteRT-LM, which does not report its executing backend)
 *   mismatch    the engine reported a different backend than requested
 *   unknown     no usable evidence
 *
 * Mirrored by scripts/raw_session.py (assess_backend); keep in step.
 */
import type {BackendId, EngineId} from './types';

export type BackendStatus = 'confirmed' | 'consistent' | 'mismatch' | 'unknown';

export interface EngineReport {
  gpu: boolean | null;
  devices: string[] | null;
  androidLib: string | null;
  /** Free engine text; kept in raw evidence, never exported. */
  reasonNoGpu: string | null;
}

export interface BackendEvidence {
  status: BackendStatus;
  source: 'engine-report' | 'process-libraries' | 'none';
  report: EngineReport | null;
  libraries: {before: string[] | null; after: string[] | null} | null;
}

export const NPU_DISPATCH_LIB = 'libLiteRtDispatch_Qualcomm.so';
export const OPENCL_LIB = 'libOpenCL.so';

export function assessBackend(engine: EngineId, requested: BackendId,
  evidence: Pick<BackendEvidence, 'report' | 'libraries'>): BackendStatus {
  if (engine === 'llama.cpp') {
    const report = evidence.report;
    if (!report || report.gpu === null) return 'unknown';
    // llama.rn's gpu flag means GPU devices are attached to the model, not that layers run
    // there. A CPU run (nGpuLayers 0) on a build with OpenCL/Hexagon still attaches them, and
    // neither backend implements op offload, so attached-but-unused is consistent, not proof.
    if (requested === 'cpu') return report.gpu ? 'consistent' : 'confirmed';
    if (!report.gpu) return 'mismatch';
    if (report.devices === null || !report.devices.length) return 'consistent';
    const htp = report.devices.filter(device => device.toUpperCase().startsWith('HTP')).length;
    if (requested === 'gpu') return htp === 0 ? 'confirmed' : 'mismatch';
    return htp === report.devices.length ? 'confirmed' : 'mismatch';
  }
  if (engine === 'litert-lm') {
    // Only a runtime loaded by this engine creation counts: one already mapped (by the gate,
    // an earlier run or llama.rn's OpenCL variant) says nothing about this engine.
    const after = evidence.libraries?.after ?? null;
    const before = evidence.libraries?.before ?? null;
    if (after === null || before === null) return 'unknown';
    const loaded = after.filter(name => !before.includes(name));
    if (requested === 'npu') return loaded.includes(NPU_DISPATCH_LIB) ? 'consistent' : 'unknown';
    if (requested === 'gpu') return loaded.includes(OPENCL_LIB) ? 'consistent' : 'unknown';
    return loaded.includes(NPU_DISPATCH_LIB) || loaded.includes(OPENCL_LIB) ? 'unknown' : 'consistent';
  }
  return 'unknown';
}

export function backendEvidence(engine: EngineId, requested: BackendId,
  source: BackendEvidence['source'], report: EngineReport | null,
  libraries: BackendEvidence['libraries']): BackendEvidence {
  return {status: assessBackend(engine, requested, {report, libraries}), source, report, libraries};
}

const RANK: Record<BackendStatus, number> = {unknown: 0, consistent: 1, confirmed: 2, mismatch: 3};

/** Session summary: any mismatch wins; otherwise the weakest evidence across repetitions. */
export function summarizeBackend(evidence: BackendEvidence[]):
  {status: BackendStatus; source: BackendEvidence['source']; repetitions: number} | null {
  if (!evidence.length) return null;
  const statuses = evidence.map(item => item.status);
  const status = statuses.includes('mismatch') ? 'mismatch' :
    statuses.reduce((low, item) => RANK[item] < RANK[low] ? item : low, statuses[0]);
  const sources = [...new Set(evidence.map(item => item.source))];
  return {status, source: sources.length === 1 ? sources[0] : 'none', repetitions: evidence.length};
}
