import React, { useState } from 'react';
import { Period, UsageReport } from '../types';
import { Terminal, Bot, Sparkles, MessageSquare, Code, ChevronDown, ChevronUp, Eye, EyeOff } from 'lucide-react';
import { ALLOWED_TOOLS } from './OverviewCards';
import { formatTokens } from '../utils';

interface ToolCardListProps {
  usage: UsageReport;
  period: Period;
}

const TOOL_META: Record<string, { name: string; icon: any; color: string; bg: string }> = {
  codex:     { name: 'Codex CLI',             icon: Terminal,     color: 'text-emerald-500', bg: 'bg-emerald-500/10 border-emerald-500/20' },
  claude:    { name: 'Claude Code',           icon: Bot,          color: 'text-amber-500',   bg: 'bg-amber-500/10 border-amber-500/20'   },
  gemini:    { name: 'Gemini / Antigravity',  icon: Sparkles,     color: 'text-sky-500',     bg: 'bg-sky-500/10 border-sky-500/20'       },
  cursor:    { name: 'Cursor Composer',       icon: Terminal,     color: 'text-fuchsia-500', bg: 'bg-fuchsia-500/10 border-fuchsia-500/20'},
  opencode:  { name: 'OpenCode (DeepSeek)',   icon: Code,         color: 'text-violet-500',  bg: 'bg-violet-500/10 border-violet-500/20' },
  codebuddy: { name: 'CodeBuddy',             icon: Bot,          color: 'text-orange-500',  bg: 'bg-orange-500/10 border-orange-500/20' },
  kimicode:  { name: 'Kimi Code',             icon: Bot,          color: 'text-cyan-500',    bg: 'bg-cyan-500/10 border-cyan-500/20'     },
  grok:      { name: 'Grok Build',            icon: MessageSquare,color: 'text-rose-500',    bg: 'bg-rose-500/10 border-rose-500/20'     },
  grokbuild: { name: 'Grok Build',            icon: MessageSquare,color: 'text-rose-500',    bg: 'bg-rose-500/10 border-rose-500/20'     },
  pi:        { name: 'Pi Agent CLI',          icon: Code,         color: 'text-indigo-500',  bg: 'bg-indigo-500/10 border-indigo-500/20' },
  hermes:    { name: 'Hermes Agent',          icon: Terminal,     color: 'text-teal-500',    bg: 'bg-teal-500/10 border-teal-500/20'     },
  qoder:     { name: 'Qoder IDE',             icon: Sparkles,     color: 'text-blue-500',    bg: 'bg-blue-500/10 border-blue-500/20'     },
};

export const ToolCardList: React.FC<ToolCardListProps> = ({ usage, period }) => {
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({});
  const [showAllConfigured, setShowAllConfigured] = useState(false);

  const toggleExpand = (key: string) => {
    setExpandedCards((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // 1. 严格只保留本地安装/支持的 8 个真实编程工具，过滤掉所有冗余虚构工具
  const allTools = ALLOWED_TOOLS.filter((key) => usage[key]?.ranges?.[period]).map((key) => [
    key,
    usage[key],
  ]) as [string, any][];

  // 2. 区分当前周期内“有消耗/活跃的工具”与“零用量工具”
  const activeTools = allTools.filter(([key, val]) => {
    const r = val.ranges[period];
    const cacheTokens = (r.cr || 0) + (r.cached || 0);
    const totalTokens = (r.in || 0) + cacheTokens + (r.out || 0) + (r.reason || 0);
    return totalTokens > 0 || (r.cost || 0) > 0 || (r.sessions || 0) > 0;
  });

  const inactiveToolsCount = allTools.length - activeTools.length;
  const displayList = showAllConfigured ? allTools : activeTools;

  // 排序：按当前周期产生花费由高到低
  displayList.sort((a, b) => {
    const costA = a[1].ranges[period]?.cost || 0;
    const costB = b[1].ranges[period]?.cost || 0;
    if (costB !== costA) return costB - costA;
    return (b[1].ranges[period]?.sessions || 0) - (a[1].ranges[period]?.sessions || 0);
  });

  return (
    <div className="mb-6">
      {/* Title & Filter Toggle */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100 flex items-center gap-2">
          <span>AI 编程工具用量明细</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium border border-emerald-500/20">
            {activeTools.length} 个活跃工具
          </span>
        </h3>

        {inactiveToolsCount > 0 && (
          <button
            onClick={() => setShowAllConfigured(!showAllConfigured)}
            className="text-xs text-slate-500 dark:text-zinc-400 hover:text-slate-800 dark:hover:text-zinc-200 flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-200 dark:border-zinc-800 bg-white dark:bg-[#181820] transition-colors"
          >
            {showAllConfigured ? (
              <>
                <EyeOff className="w-3.5 h-3.5" />
                <span>仅看活跃工具</span>
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5" />
                <span>展开未活跃工具 ({inactiveToolsCount})</span>
              </>
            )}
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3.5">
        {displayList.map(([key, val]) => {
          const r = val.ranges[period];
          const meta = TOOL_META[key] || {
            name: key,
            icon: Terminal,
            color: 'text-slate-500 dark:text-zinc-400',
            bg: 'bg-slate-100 dark:bg-zinc-800 border-slate-200 dark:border-zinc-700',
          };
          const Icon = meta.icon;

          // 核心 Token 逻辑修复：
          // 1. cacheTokens 为缓存读取 Token (cr 或 cached)
          // 2. fullPrompt 为完整输入的 Prompt 大小 (即 未缓存输入 + 缓存输入)
          // 3. totalTokens 为该工具产生的所有总吞吐 Token (Prompt输入 + Completion输出)
          const cacheTokens = (r.cr || 0) + (r.cached || 0);
          const fullPrompt = (r.in || 0) + cacheTokens;
          const totalTokens = fullPrompt + (r.out || 0) + (r.reason || 0);

          // 过滤掉无消耗的无效模型 (如 未知: in=0, out=0)
          const validModels = (r.models || []).filter((m: any) => {
            const mCache = (m.cr || 0) + (m.cached || 0);
            const mTot = (m.in || 0) + mCache + (m.out || 0) + (m.reason || 0);
            return mTot > 0 || (m.cost || 0) > 0;
          });

          const isExpanded = expandedCards[key];
          const visibleModels = isExpanded ? validModels : validModels.slice(0, 3);

          return (
            <div
              key={key}
              className="bg-white dark:bg-[#181820] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 hover:border-slate-300 dark:hover:border-zinc-700 transition-all hover:shadow-md flex flex-col justify-between"
            >
              <div>
                {/* Header */}
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2.5">
                    <div className={`p-2 rounded-lg border ${meta.bg}`}>
                      <Icon className={`w-4 h-4 ${meta.color}`} />
                    </div>
                    <div>
                      <h4 className="text-sm font-medium text-slate-800 dark:text-zinc-100">{meta.name}</h4>
                      <div className="text-[11px] text-slate-500 dark:text-zinc-400 flex items-center gap-1">
                        <span>{r.sessions || 0} 场独立会话</span>
                        {r.turns ? <span>· {r.turns} 次交互</span> : null}
                        {r.tools ? <span>· {r.tools} 次工具调用</span> : null}
                      </div>
                    </div>
                  </div>

                  <div className="text-right">
                    <div className="text-base font-bold text-slate-900 dark:text-white font-mono">
                      ${(r.cost || 0).toFixed(2)}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-zinc-400">
                      缓存率 {(r.hit || 0).toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* 4 维指标栅格：总吞吐量 + Prompt输入 + 输出补全 + 缓存读取 */}
                <div className="bg-slate-50 dark:bg-[#121216] rounded-lg p-2.5 grid grid-cols-4 gap-1.5 text-center text-xs mb-3 border border-slate-200/80 dark:border-zinc-800/40">
                  <div className="border-r border-slate-200 dark:border-zinc-800/60 pr-1">
                    <span className="text-[10px] text-slate-500 dark:text-zinc-400 block font-medium">总吞吐量</span>
                    <span className="font-mono text-slate-900 dark:text-white font-bold text-xs">{formatTokens(totalTokens)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-500 dark:text-zinc-400 block">Prompt 输入</span>
                    <span className="font-mono text-slate-800 dark:text-zinc-200 font-semibold">{formatTokens(fullPrompt)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-500 dark:text-zinc-400 block">输出补全</span>
                    <span className="font-mono text-slate-800 dark:text-zinc-200 font-semibold">{formatTokens(r.out || 0)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-500 dark:text-zinc-400 block">缓存读取</span>
                    <span className="font-mono text-purple-600 dark:text-purple-400 font-semibold">{formatTokens(cacheTokens)}</span>
                  </div>
                </div>

                {/* Models Detail Breakdown */}
                {validModels.length > 0 && (
                  <div className="space-y-1.5 pt-2 border-t border-slate-100 dark:border-zinc-800/40">
                    <div className="flex items-center justify-between text-[10px] font-semibold text-slate-500 dark:text-zinc-400">
                      <span>核心模型 Token 细分</span>
                      <span>输入 / 输出 / 缓存 → 总计</span>
                    </div>
                    <div className="space-y-1">
                      {visibleModels.map((m: any, idx: number) => {
                        const mCache = (m.cr || 0) + (m.cached || 0);
                        const mPrompt = (m.in || 0) + mCache;
                        const mTotal = mPrompt + (m.out || 0) + (m.reason || 0);

                        return (
                          <div
                            key={idx}
                            className="flex items-center justify-between text-xs bg-slate-50/70 dark:bg-[#14141a] px-2.5 py-1.5 rounded-lg border border-slate-200/70 dark:border-zinc-800/60"
                          >
                            <div className="min-w-0 pr-2">
                              <span className="font-medium text-slate-800 dark:text-zinc-200 block truncate" title={m.name}>
                                {m.name}
                              </span>
                              <span className="text-[10px] text-slate-500 dark:text-zinc-400 font-mono block">
                                入: {formatTokens(mPrompt)} · 出: {formatTokens(m.out || 0)} · 缓: {formatTokens(mCache)}
                              </span>
                            </div>
                            <div className="text-right flex-shrink-0 font-mono">
                              <div className="text-slate-900 dark:text-zinc-100 font-bold">{formatTokens(mTotal)}</div>
                              {m.cost > 0 && (
                                <div className="text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold">${m.cost.toFixed(2)}</div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* Expand Toggle */}
              {validModels.length > 3 && (
                <button
                  onClick={() => toggleExpand(key)}
                  className="mt-2 text-[11px] text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 flex items-center justify-center gap-1 py-1 w-full rounded hover:bg-slate-100 dark:hover:bg-zinc-800/40 transition-colors"
                >
                  {isExpanded ? (
                    <>
                      <span>收起模型</span>
                      <ChevronUp className="w-3 h-3" />
                    </>
                  ) : (
                    <>
                      <span>查看其余 {validModels.length - 3} 个模型 Token</span>
                      <ChevronDown className="w-3 h-3" />
                    </>
                  )}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
