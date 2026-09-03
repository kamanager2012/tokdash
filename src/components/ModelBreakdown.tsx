import React, { useState } from 'react';
import { TopModelRecord } from '../types';
import { Layers, ChevronDown, ChevronUp } from 'lucide-react';
import { formatTokens, getCacheReadTokens } from '../utils';

interface ModelBreakdownProps {
  models: TopModelRecord[];
}

export const ModelBreakdown: React.FC<ModelBreakdownProps> = ({ models = [] }) => {
  const [expanded, setExpanded] = useState(false);

  // Filter out any models that have 0 usage across all dimensions
  const validModels = models.filter((m) => {
    const cr = getCacheReadTokens(m);
    const tot = (m.tokens || 0) + (m.in || 0) + cr + (m.out || 0) + (m.reason || 0);
    return tot > 0 || (m.cost || 0) > 0;
  });

  const displayedModels = expanded ? validModels : validModels.slice(0, 10);
  const maxTokens = Math.max(...validModels.map((m) => m.tokens || 0), 1);

  if (validModels.length === 0) return null;

  return (
    <div className="bg-white dark:bg-[#14141c] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-5 shadow-sm transition-colors mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-500/10 text-indigo-500 flex items-center justify-center">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100">
              模型消耗排行榜
            </h3>
            <span className="text-[11px] text-slate-500 dark:text-zinc-400">
              按当前时间段内总 Token 吞吐排序
            </span>
          </div>
        </div>

        {validModels.length > 10 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-indigo-500 dark:text-indigo-400 hover:text-indigo-600 dark:hover:text-indigo-300 font-medium flex items-center gap-1 transition-colors"
          >
            {expanded ? (
              <>
                <span>收起</span>
                <ChevronUp className="w-3.5 h-3.5" />
              </>
            ) : (
              <>
                <span>查看全部 ({validModels.length})</span>
                <ChevronDown className="w-3.5 h-3.5" />
              </>
            )}
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-slate-100 dark:border-zinc-800/80 text-slate-500 dark:text-zinc-400">
              <th className="pb-2.5 font-medium">模型名称 / 工具</th>
              <th className="pb-2.5 font-medium text-right">总吞吐 Token</th>
              <th className="pb-2.5 font-medium text-right">Prompt 输入 (含缓存)</th>
              <th className="pb-2.5 font-medium text-right">输出补全</th>
              <th className="pb-2.5 font-medium text-right">其中缓存读取</th>
              <th className="pb-2.5 font-medium text-right">产生花费</th>
              <th className="pb-2.5 font-medium text-right w-36">用量占比</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-zinc-800/40 font-mono">
            {displayedModels.map((m, idx) => {
              const cacheRead = getCacheReadTokens(m);
              const barWidth = Math.min(100, Math.max(3, ((m.tokens || 0) / maxTokens) * 100));
              const isHeuristic = m.tool?.toLowerCase() === 'cursor' || m.name.includes('(Cursor)');

              return (
                <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-zinc-800/20 transition-colors">
                  <td className="py-2.5 font-sans">
                    <div className="flex items-center gap-2">
                      <span className="w-4 text-slate-400 dark:text-zinc-500 text-[11px] font-mono">{idx + 1}.</span>
                      <span className="font-semibold text-slate-800 dark:text-zinc-200">{m.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-zinc-800 text-slate-500 dark:text-zinc-400 uppercase font-mono">
                        {m.tool}
                      </span>
                      {isHeuristic && (
                        <span
                          className="text-[9px] px-1 py-0.2 rounded bg-sky-500/10 text-sky-600 dark:text-sky-400 font-sans"
                          title="基于本地字符数启发式推算 (chars/4)，非官方精确账单"
                        >
                          推算
                        </span>
                      )}
                      {(() => {
                        const prov = m.pricing_provenance;
                        if (prov === 'exact_catalog' || prov === 'exact_alias') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-sans"
                              title={`官方/目录精确计费: ${m.pricing_source || m.name}`}
                            >
                              精确
                            </span>
                          );
                        }
                        if (prov === 'price_equivalent') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-sans"
                              title={`替代费率等价映射: ${m.pricing_source || m.name}`}
                            >
                              等价
                            </span>
                          );
                        }
                        if (prov === 'manual_proxy') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 font-sans"
                              title={`人工指定代理模型: ${m.pricing_source || m.name}`}
                            >
                              代理
                            </span>
                          );
                        }
                        if (prov === 'family_proxy') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 font-sans"
                              title={`同家族关键词代理: ${m.pricing_source || m.name}`}
                            >
                              家族代理
                            </span>
                          );
                        }
                        if (prov === 'authoritative') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-teal-500/10 text-teal-600 dark:text-teal-400 font-sans"
                              title="本地日志权威入账"
                            >
                              权威账本
                            </span>
                          );
                        }
                        if (prov === 'unknown') {
                          return (
                            <span
                              className="text-[9px] px-1 py-0.2 rounded bg-slate-500/10 text-slate-500 dark:text-slate-400 font-sans"
                              title="未收录价格（按 0 记费）"
                            >
                              未知费率
                            </span>
                          );
                        }
                        return null;
                      })()}
                    </div>
                  </td>
                  <td className="py-2.5 text-right font-bold text-slate-900 dark:text-zinc-100">
                    {isHeuristic ? '~' : ''}{formatTokens(m.tokens || 0)}
                  </td>
                  <td className="py-2.5 text-right text-slate-700 dark:text-zinc-300">
                    {formatTokens(m.in || 0)}
                  </td>
                  <td className="py-2.5 text-right text-slate-700 dark:text-zinc-300">
                    {formatTokens(m.out || 0)}
                  </td>
                  <td className="py-2.5 text-right text-purple-600 dark:text-purple-400">
                    {formatTokens(cacheRead)}
                  </td>
                  <td className="py-2.5 text-right font-semibold text-emerald-600 dark:text-emerald-400">
                    {isHeuristic ? '~' : ''}${(m.cost || 0).toFixed(2)}
                  </td>
                  <td className="py-2.5 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <div className="w-20 bg-slate-100 dark:bg-zinc-900 h-1.5 rounded-full overflow-hidden">
                        <div
                          className="bg-gradient-to-r from-sky-500 to-purple-500 h-full rounded-full"
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                      <span className="text-[10px] text-slate-400 dark:text-zinc-400 w-8">
                        {((m.tokens / maxTokens) * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Show more toggle */}
      {validModels.length > 10 && (
        <div className="mt-3 pt-3 border-t border-slate-100 dark:border-zinc-800/60 flex justify-center">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-slate-500 dark:text-zinc-400 hover:text-slate-800 dark:hover:text-zinc-200 flex items-center gap-1.5 py-1 px-3 rounded-lg hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors font-medium"
          >
            {expanded ? (
              <>
                <span>收起部分模型</span>
                <ChevronUp className="w-3.5 h-3.5" />
              </>
            ) : (
              <>
                <span>展开全部 {validModels.length} 个模型明细</span>
                <ChevronDown className="w-3.5 h-3.5" />
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
