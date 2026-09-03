import React from 'react';
import { Period, UsageReport } from '../types';
import { DollarSign, Cpu, Zap, Layers } from 'lucide-react';

interface OverviewCardsProps {
  usage: UsageReport;
  period: Period;
}

export const ALLOWED_TOOLS = [
  'codex',
  'gemini',
  'cursor',
  'claude',
  'opencode',
  'codebuddy',
  'kimicode',
  'grok',
  'pi',
  'hermes',
  'qoder',
];

function formatNumber(num: number): string {
  if (num >= 1_000_000_000) return (num / 1_000_000_000).toFixed(2) + 'B';
  if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
  if (num >= 1_000) return (num / 1_000).toFixed(1) + 'K';
  return (num || 0).toLocaleString();
}

export const OverviewCards: React.FC<OverviewCardsProps> = ({ usage, period }) => {
  let totalCost = 0;
  let totalUncachedIn = 0;
  let totalOut = 0;
  let totalCacheRead = 0;
  let totalSessions = 0;

  Object.entries(usage).forEach(([key, val]) => {
    if (!ALLOWED_TOOLS.includes(key)) return;
    if (key.startsWith('_') || !val?.ranges) return;
    const r = val.ranges[period];
    if (!r) return;

    const crVal = (r.cr || 0) + (r.cached || 0);
    totalCost += r.cost || 0;
    totalUncachedIn += r.in || 0;
    totalOut += r.out || 0;
    totalCacheRead += crVal;
    totalSessions += r.sessions || 0;
  });

  const totalPrompt = totalUncachedIn + totalCacheRead;
  const totalTokens = totalPrompt + totalOut;
  const cacheHitRate = totalPrompt > 0
    ? ((totalCacheRead / totalPrompt) * 100).toFixed(1)
    : '0.0';

  return (
    <div className="grid grid-cols-4 gap-3.5 mb-6">
      {/* Total Cost */}
      <div className="bg-white dark:bg-gradient-to-b dark:from-[#1c1c24] dark:to-[#15151c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm hover:border-slate-300 dark:hover:border-zinc-700 transition-all">
        <div className="flex items-center justify-between text-slate-500 dark:text-zinc-400 mb-2">
          <span className="text-xs font-medium">总 API 消耗</span>
          <div className="w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-500 flex items-center justify-center">
            <DollarSign className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
          ${totalCost.toFixed(2)}
        </div>
        <div className="mt-1 text-[11px] text-slate-400 dark:text-zinc-400">
          基于官方与开源真实费率
        </div>
      </div>

      {/* Total Tokens */}
      <div className="bg-white dark:bg-gradient-to-b dark:from-[#1c1c24] dark:to-[#15151c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm hover:border-slate-300 dark:hover:border-zinc-700 transition-all">
        <div className="flex items-center justify-between text-slate-500 dark:text-zinc-400 mb-2">
          <span className="text-xs font-medium">吞吐总 Token</span>
          <div className="w-7 h-7 rounded-lg bg-sky-500/10 text-sky-500 flex items-center justify-center">
            <Cpu className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
          {formatNumber(totalTokens)}
        </div>
        <div className="mt-1 text-[11px] text-slate-400 dark:text-zinc-400 flex items-center gap-1.5">
          <span>总入: {formatNumber(totalPrompt)}</span>
          <span>·</span>
          <span>出: {formatNumber(totalOut)}</span>
        </div>
      </div>

      {/* Cache Hit Rate */}
      <div className="bg-white dark:bg-gradient-to-b dark:from-[#1c1c24] dark:to-[#15151c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm hover:border-slate-300 dark:hover:border-zinc-700 transition-all">
        <div className="flex items-center justify-between text-slate-500 dark:text-zinc-400 mb-2">
          <span className="text-xs font-medium">缓存命中率</span>
          <div className="w-7 h-7 rounded-lg bg-purple-500/10 text-purple-500 flex items-center justify-center">
            <Zap className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
          {cacheHitRate}%
        </div>
        <div className="mt-1 text-[11px] text-slate-400 dark:text-zinc-400">
          已节省读取: {formatNumber(totalCacheRead)} Tokens
        </div>
      </div>

      {/* Active Sessions */}
      <div className="bg-white dark:bg-gradient-to-b dark:from-[#1c1c24] dark:to-[#15151c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm hover:border-slate-300 dark:hover:border-zinc-700 transition-all">
        <div className="flex items-center justify-between text-slate-500 dark:text-zinc-400 mb-2">
          <span className="text-xs font-medium">活跃会话数</span>
          <div className="w-7 h-7 rounded-lg bg-amber-500/10 text-amber-500 flex items-center justify-center">
            <Layers className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
          {totalSessions.toLocaleString()} 场
        </div>
        <div className="mt-1 text-[11px] text-slate-400 dark:text-zinc-400">
          多轮深度交互与并发任务
        </div>
      </div>
    </div>
  );
};
