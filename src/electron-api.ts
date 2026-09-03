import type {
  DailyCostRecord,
  ProjectRecord,
  TopModelRecord,
  UsageReport,
} from './types';

export interface DailyCostsEnvelope {
  daily: DailyCostRecord[];
  models?: TopModelRecord[];
}

export type DailyCostsPayload = DailyCostRecord[] | DailyCostsEnvelope;

export interface SnapshotPayload {
  snapshot_id: string;
  generation: string;
  generated_at: string;
  usage: UsageReport;
  daily_costs: DailyCostsPayload;
  projects: ProjectRecord[];
}

export interface TokDashBridge {
  fetchSnapshot(): Promise<SnapshotPayload>;
  updatePrices(): Promise<unknown>;
  minimizeWindow(): Promise<void>;
  closeWindow(): Promise<void>;
  toggleMaximize(): Promise<void>;
}
