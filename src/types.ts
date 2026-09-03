export type Period = 'today' | 'yesterday' | '7d' | '30d' | 'week' | 'month' | 'year' | 'all';

export interface ProjectRecord {
  path: string;
  name: string;
  last_active: string;
  sessions: number;
  tokens: number;
  cost: number;
  top_model: string;
  tools: string[];
  ports?: number[];
}

export interface ModelUsage {
  model_id: string;
  name: string;
  in: number;
  out: number;
  cr: number;
  cw: number;
  reason: number;
  cost: number;
  pin?: number;
  pout?: number;
}

export interface ToolRange {
  hit: number;
  in: number;
  out: number;
  cr: number;
  cw: number;
  reason: number;
  cost: number;
  sessions: number;
  models: ModelUsage[];
}

export interface ToolData {
  ranges: Record<Period, ToolRange>;
}

export type UsageReport = Record<string, any>;

export interface DailyCostRecord {
  date: string;
  total: number;
  tokens: number;
  claude?: number;
  codex?: number;
  gemini?: number;
  grok?: number;
  pi?: number;
  opencode?: number;
  kimicode?: number;
  hermes?: number;
  [key: string]: any;
}

export interface TopModelRecord {
  name: string;
  cost: number;
  in: number;
  out: number;
  cr: number;
  cw: number;
  cached?: number; // legacy alias for cr, may be present from older backend versions
  reason: number;
  tokens: number;
  tool: string;
  cost_per_k: number;
  out_ratio: number;
}

export interface DailyCostsResponse {
  daily: DailyCostRecord[];
  models: TopModelRecord[];
}
