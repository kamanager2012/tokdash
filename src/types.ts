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

export type PricingProvenance =
  | 'exact_catalog'
  | 'exact_alias'
  | 'price_equivalent'
  | 'manual_proxy'
  | 'family_proxy'
  | 'authoritative'
  | 'unknown';

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
  pricing_provenance?: PricingProvenance;
  pricing_source?: string;
  cost_kind?: string;
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

export interface UsageReport {
  cursor?: ToolData & { model?: string; quota?: CursorQuota; estimated?: boolean; provenance?: string };
  cursor_quota?: CursorQuota;
  claude_plan?: any;
  codex_reset_cards?: any;
  _errors?: Record<string, string>;
  _pricing?: { updated_at: string; count: number };
  [tool: string]: any;
}

export interface DailyCostRecord {
  date: string;
  total: number;
  tokens: number;
  tool_costs?: Record<string, number>;
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
  pricing_provenance?: PricingProvenance;
  pricing_source?: string;
  cost_kind?: string;
}

export interface CursorQuotaWindow {
  id?: string;
  name?: string;
  used_pct?: number;
  reset?: number;
  detail?: string;
  window_minutes?: number;
  spend?: number;
  limit?: number;
}

export interface CursorQuotaDetail {
  label: string;
  value: string;
  name?: string;
  used?: number;
  total?: number;
  unit?: string;
}

export interface CursorQuota {
  available?: boolean;
  plan?: string;
  account?: string;
  windows?: CursorQuotaWindow[];
  details?: CursorQuotaDetail[];
  source?: string;
  updated?: number;
  stale?: boolean;
  // Legacy fallback fields
  percent_used?: number;
  total_spend?: number;
  included_spend?: number;
  bonus_spend?: number;
  end?: number;
}

export interface DailyCostsResponse {
  daily: DailyCostRecord[];
  models: TopModelRecord[];
}
