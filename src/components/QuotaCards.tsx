import React from 'react';
import { UsageReport, CursorQuota, CursorQuotaWindow, CursorQuotaDetail } from '../types';
import { ShieldCheck, Clock, Sparkles, Terminal, MessageSquare } from 'lucide-react';

interface QuotaCardsProps {
  usage: UsageReport;
}

function formatCountdown(epochSeconds: number | null): string {
  if (!epochSeconds) return '就绪';
  const now = Math.floor(Date.now() / 1000);
  const diff = epochSeconds - now;
  if (diff <= 0) return '已刷新';
  const hours = Math.floor(diff / 3600);
  const minutes = Math.floor((diff % 3600) / 60);
  if (hours > 24) {
    const days = Math.floor(hours / 24);
    return `${days}天 ${hours % 24}小时后刷新`;
  }
  return `${hours}小时 ${minutes}分后刷新`;
}

export const QuotaCards: React.FC<QuotaCardsProps> = ({ usage }) => {
  const ag = usage.antigravity;
  const codex = usage.codex;
  const grok = usage.grok;
  const cursor: CursorQuota | undefined = usage.cursor_quota;

  if (!ag && !codex && !grok && !cursor) return null;

  return (
    <div className="mb-6 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-sky-500" />
          <span>官方套餐订阅与窗口额度 (Quota & Rate Limits)</span>
        </h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {/* Antigravity Google AI Pro Card */}
        {ag && ag.available && (
          <div className="bg-white dark:bg-[#181822] border border-sky-400/40 dark:border-sky-500/20 rounded-xl p-4 shadow-sm relative overflow-hidden transition-colors">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-sky-500/10 text-sky-500">
                  <Sparkles className="w-4 h-4" />
                </div>
                <div>
                  <span className="text-xs font-semibold text-slate-800 dark:text-zinc-100 block">
                    Antigravity
                  </span>
                  <span className="text-[10px] text-sky-600 dark:text-sky-400 font-medium">
                    {ag.plan || 'Google AI Pro'}
                  </span>
                </div>
              </div>
              <span className="text-[10px] text-slate-500 dark:text-zinc-400 font-mono truncate max-w-[120px]" title={ag.account}>
                {ag.account}
              </span>
            </div>

            <div className="space-y-2 mt-3 pt-2 border-t border-slate-100 dark:border-zinc-800/60">
              {(ag.windows || []).slice(0, 3).map((w: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between text-xs">
                  <span className="text-slate-600 dark:text-zinc-300 flex items-center gap-1.5">
                    <Clock className="w-3 h-3 text-slate-400 dark:text-zinc-400" />
                    {w.title}
                  </span>
                  <span className="text-[11px] text-sky-600 dark:text-sky-300 font-mono font-medium">
                    {w.detail ? w.detail.replace('You have used some of your ', '').replace(' limit, it will fully refresh in ', '刷新: ') : formatCountdown(w.reset)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Codex Plus / Pro Card */}
        {codex && (
          <div className="bg-white dark:bg-[#181822] border border-emerald-400/40 dark:border-emerald-500/20 rounded-xl p-4 shadow-sm transition-colors">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-500">
                  <Terminal className="w-4 h-4" />
                </div>
                <div>
                  <span className="text-xs font-semibold text-slate-800 dark:text-zinc-100 block">
                    Codex
                  </span>
                  <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium uppercase">
                    ChatGPT {codex.plan || 'Plus'}
                  </span>
                </div>
              </div>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-mono font-medium">
                活跃中
              </span>
            </div>

            <div className="space-y-2 mt-3 pt-2 border-t border-slate-100 dark:border-zinc-800/60">
              <div className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500 dark:text-zinc-400">5 小时使用率</span>
                  <span className="text-slate-700 dark:text-zinc-200 font-mono font-semibold">
                    {codex.p5 !== null && codex.p5 !== undefined ? `${codex.p5}%` : '充沛'}
                  </span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-zinc-900 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-emerald-500 h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.min(100, Math.max(5, codex.p5 || 5))}%` }}
                  />
                </div>
              </div>
              {codex.pw !== undefined && codex.pw !== null && (
                <div className="flex justify-between text-xs pt-1">
                  <span className="text-slate-500 dark:text-zinc-400">周使用率</span>
                  <span className="text-slate-700 dark:text-zinc-200 font-mono font-semibold">{codex.pw}%</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Grok Quota Card */}
        {grok && (
          <div className="bg-white dark:bg-[#181822] border border-rose-400/40 dark:border-rose-500/20 rounded-xl p-4 shadow-sm transition-colors">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-rose-500/10 text-rose-500">
                  <MessageSquare className="w-4 h-4" />
                </div>
                <div>
                  <span className="text-xs font-semibold text-slate-800 dark:text-zinc-100 block">
                    Grok
                  </span>
                  <span className="text-[10px] text-rose-600 dark:text-rose-400 font-medium">
                    {grok.plan || 'xAI Platform'}
                  </span>
                </div>
              </div>
              {grok.pct !== null && grok.pct !== undefined && (
                <span className="text-[11px] font-mono font-semibold text-rose-600 dark:text-rose-400">
                  已用 {Number(grok.pct).toFixed(0)}% <span className="text-[10px] text-slate-500 dark:text-zinc-400 font-normal">(余 {Math.max(0, 100 - Number(grok.pct)).toFixed(0)}%)</span>
                </span>
              )}
            </div>

            <div className="space-y-2 mt-3 pt-2 border-t border-slate-100 dark:border-zinc-800/60">
              <div className="space-y-1">
                <div className="w-full bg-slate-100 dark:bg-zinc-900 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-rose-500 h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.min(100, Math.max(0, Number(grok.pct) || 0))}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between text-xs pt-1">
                <span className="text-slate-500 dark:text-zinc-400">重置时间</span>
                <span className="text-slate-700 dark:text-zinc-300 font-mono text-[11px]">
                  {formatCountdown(grok.reset)}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Cursor Official Quota Card */}
        {cursor && cursor.available && (() => {
          const totalWindow = cursor.windows?.find((w: CursorQuotaWindow) => w.id === 'cursor-total') || cursor.windows?.[0];
          const usedPct = totalWindow?.used_pct ?? cursor.percent_used ?? 0;
          const resetTime = totalWindow?.reset ?? cursor.end;
          
          // Plan display: avoid "Cursor Cursor Ultra" duplication
          const rawPlan = cursor.plan || 'Ultra';
          const displayPlan = rawPlan.toLowerCase().startsWith('cursor ') ? rawPlan : `Cursor ${rawPlan}`;

          // Detailed metrics resolution
          const packageDetail = cursor.details?.find((d: CursorQuotaDetail) => d.label === '套餐用量')?.value;
          const budgetDetail = cursor.details?.find((d: CursorQuotaDetail) => d.label === '按量预算')?.value;
          const spendText = packageDetail || (cursor.total_spend !== undefined ? `$${cursor.total_spend}` : null);
          const subText = budgetDetail ? `按量: ${budgetDetail}` : (
            cursor.included_spend !== undefined
              ? `包含 $${cursor.included_spend} + 赠送 $${cursor.bonus_spend ?? 0}`
              : '订阅额度'
          );

          return (
            <div className="bg-white dark:bg-[#181822] border border-fuchsia-400/40 dark:border-fuchsia-500/20 rounded-xl p-4 shadow-sm transition-colors">
              <div className="flex items-center justify-between mb-2.5">
                <div className="flex items-center gap-2">
                  <div className="p-1.5 rounded-lg bg-fuchsia-500/10 text-fuchsia-500">
                    <Terminal className="w-4 h-4" />
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-slate-800 dark:text-zinc-100 block">
                      Cursor
                    </span>
                    <span className="text-[10px] text-fuchsia-600 dark:text-fuchsia-400 font-medium">
                      {displayPlan}
                    </span>
                  </div>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400 font-mono font-medium">
                  已用 {Number(usedPct).toFixed(1)}%
                </span>
              </div>

              <div className="space-y-2 mt-3 pt-2 border-t border-slate-100 dark:border-zinc-800/60">
                <div className="space-y-1">
                  {spendText && (
                    <div className="flex justify-between text-xs">
                      <span className="text-slate-500 dark:text-zinc-400">当前周期用量</span>
                      <span className="text-slate-700 dark:text-zinc-200 font-mono font-semibold">
                        {spendText}
                      </span>
                    </div>
                  )}
                  <div className="w-full bg-slate-100 dark:bg-zinc-900 h-1.5 rounded-full overflow-hidden">
                    <div
                      className="bg-fuchsia-500 h-full rounded-full transition-all duration-300"
                      style={{ width: `${Math.min(100, Math.max(2, Number(usedPct) || 0))}%` }}
                    />
                  </div>
                </div>
                <div className="flex justify-between text-[11px] text-slate-500 dark:text-zinc-400 font-mono pt-0.5">
                  <span>{subText}</span>
                  {resetTime ? <span>{formatCountdown(resetTime)}</span> : <span>周期内有效</span>}
                </div>
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
};
