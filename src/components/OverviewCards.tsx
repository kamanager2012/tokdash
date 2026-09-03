import React from 'react';
import { Period, UsageReport } from '../types';
import { DollarSign, Cpu, Zap, Layers } from 'lucide-react';
import { formatTokens, calculateTotalTokens } from '../utils';

interface OverviewCardsProps {
  usage: UsageReport;
  period: Period;
}

/** Check if a report key represents a valid AI tool entry (not private metadata like _pricing or _errors). */
export function isToolKey(key: string, val: any): boolean {
  return !key.startsWith('_') && Boolean(val && typeof val === 'object' && val.ranges);
}

// Retained as fallback list for legacy components
export const ALLOWED_TOOLS = [
  'codex', 'gemini', 'cursor', 'claude', 'opencode', 'codebuddy',
  'kimicode', 'grok', 'pi', 'hermes', 'qoder', 'deepseek_harness',
  'qwencode', 'workbuddy', 'qoderwork', 'qodercli'
];


export const OverviewCards: React.FC<OverviewCardsProps> = ({ usage, period }) => {
  let totalCost = 0;
  let totalUncachedIn = 0;
  let totalOut = 0;
  let totalCacheRead = 0;
  let totalReason = 0;
  let totalSessions = 0;
  let totalTokens = 0;

  Object.entries(usage).forEach(([key, val]) => {
    // Dynamically aggregate every scanned AI tool, eliminating hardcoded drop-off
    if (!isToolKey(key, val)) return;
    const r = val.ranges?.[period];
    if (!r) return;

    const crVal = (r.cr || 0) + (r.cached || 0);
    const inVal = r.in || 0;
    const outVal = r.out || 0;
    const reasonVal = r.reason || 0;

    totalCost += r.cost || 0;
    totalUncachedIn += inVal;
    totalOut += outVal;
    totalCacheRead += crVal;
    totalReason += reasonVal;
    totalSessions += r.sessions || 0;

    // Use unified accounting: Codex reasoning is inside out, do not double-count
    totalTokens += calculateTotalTokens(key, inVal + crVal, outVal, reasonVal);
  });

  const totalPrompt = totalUncachedIn + totalCacheRead;
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
          {formatTokens(totalTokens)}
        </div>
        <div className="mt-1 text-[11px] text-slate-400 dark:text-zinc-400 flex items-center gap-1.5">
          <span>总入: {formatTokens(totalPrompt)}</span>
          <span>·</span>
          <span>出: {formatTokens(totalOut)}</span>
        </div>
      </div>

      {/* Cache Hit Rate */}
      <div className="bg-white dark:bg-gradient-to-b dark:from-[#1c1c24] dark:to-[#15151c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm hover:border-slate-300 dark:hover:border-zinc-700 transition-all">
        <div className="flex items-center justify-between text-slate-500 dark:text-zinc-400 mb-2">
          <span className="text-xs font-medium">提示词缓存率</span>
          <div className="w-7 h-7 rounded-lg bg-purple-500/10 text-purple-500 flex items-center justify-center">
            <Zap className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
          {cacheHitRate}%
        </div>
        <div className="mt-1 text-[11px] text-purple-500 dark:text-purple-400 font-medium">
          缓存读取: {formatTokens(totalCacheRead)}
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
