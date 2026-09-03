/**
 * Shared utility functions for TokDash components.
 */

/** Format a raw token count to a human-readable string (K / M / B). */
export function formatTokens(num: number): string {
  if (num >= 1_000_000_000) return (num / 1_000_000_000).toFixed(2) + 'B';
  if (num >= 1_000_000)     return (num / 1_000_000).toFixed(1) + 'M';
  if (num >= 1_000)         return (num / 1_000).toFixed(1) + 'K';
  return num.toString();
}

/** Format a USD cost value. */
export function formatCost(usd: number): string {
  if (usd === 0) return '$0.00';
  if (usd < 0.01) return '<$0.01';
  return `$${usd.toFixed(2)}`;
}

/**
 * Calculate total tokens for a tool or model turn.
 * 
 * ACCOUNTING CONTRACT:
 * - For Codex (and OpenAI-compatible outputs), `out` already encapsulates reasoning tokens.
 *   Adding `reason` on top of `out` double-counts reasoning tokens.
 * - For other tools where `reason` is an independent disjoint stream, it is added.
 */
export function calculateTotalTokens(
  toolKey: string,
  fullPrompt: number,
  out: number,
  reason: number = 0
): number {
  if (toolKey.toLowerCase() === 'codex') {
    // Codex output_tokens already includes reasoning_tokens
    return fullPrompt + (out || 0);
  }
  return fullPrompt + (out || 0) + (reason || 0);
}

/**
 * Canonical display names for all supported AI tool keys.
 * Used by TrendChart tooltip and other components that render per-tool labels.
 */
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  claude:           'Claude Code',
  codex:            'Codex CLI',
  gemini:           'Gemini / Antigravity',
  grok:             'Grok Build',
  grokbuild:        'Grok Build',
  grok_bot:         'Grok Bot',
  opencode:         'OpenCode (DeepSeek)',
  kimicode:         'Kimi Code',
  codebuddy:        'CodeBuddy',
  cursor:           'Cursor Composer',
  hermes:           'Hermes Agent',
  pi:               'Pi Agent CLI',
  antigravity:      'Antigravity',
  deepseek_harness: 'DeepSeek Harness',
  workbuddy:        'WorkBuddy',
  workbuddy_ai:     'WorkBuddy AI',
  qwencode:         'Qwen Code',
  qoder:            'Qoder IDE',
  qoderwork:        'Qoder Work',
  qodercli:         'Qoder CLI',
  prime_agent:      'Prime Agent',
  zcode:            'ZCode',
  mimocode:         'MimoCode',
  openclaw:         'OpenClaw',
};
