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
 * Canonical display names for all supported AI tool keys.
 * Used by TrendChart tooltip and other components that render per-tool labels.
 */
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  claude:           'Claude',
  codex:            'Codex',
  gemini:           'Gemini',
  grok:             'Grok Build',
  grokbuild:        'Grok Build',
  opencode:         'OpenCode',
  kimicode:         'Kimi',
  codebuddy:        'CodeBuddy',
  cursor:           'Cursor',
  hermes:           'Hermes',
  pi:               'Pi',
  antigravity:      'Antigravity',
  deepseek_harness: 'DeepSeek Harness',
  workbuddy:        'WorkBuddy',
};
