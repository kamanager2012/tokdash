import React, { useState } from 'react';
import { TopModelRecord } from '../types';
import { Layers, ChevronDown, ChevronUp, Search } from 'lucide-react';
import { formatTokens } from '../utils';

interface ModelBreakdownProps {
  models: TopModelRecord[];
}

export const ModelBreakdown: React.FC<ModelBreakdownProps> = ({ models = [] }) => {
  const [showAll, setShowAll] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const filteredModels = models.filter((m) =>
    (m.name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
    (m.tool || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const displayedModels = showAll ? filteredModels : filteredModels.slice(0, 10);
  const maxTokens = Math.max(...models.map((m) => m.tokens || 1), 1);

  if (models.length === 0) return null;

  return (
    <div className="bg-white dark:bg-[#181820] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm mb-6 transition-colors">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-purple-500/10 text-purple-500">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100">全量模型 Token 与计费透视</h3>
            <p className="text-[11px] text-slate-500 dark:text-zinc-400">
              包含 Prompt 输入、输出补全、缓存读取与推理详细 Token
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Search */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-zinc-400" />
            <input
              type="text"
              placeholder="搜索模型或工具..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bg-slate-50 dark:bg-[#121216] border border-slate-200 dark:border-zinc-800 rounded-lg pl-8 pr-3 py-1 text-xs text-slate-800 dark:text-zinc-200 placeholder-slate-400 dark:placeholder-zinc-400 focus:outline-none focus:border-purple-500/50 w-44 transition-colors"
            />
          </div>

          <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 dark:bg-zinc-800 text-slate-600 dark:text-zinc-400 font-mono">
            共 {models.length} 个模型
          </span>
        </div>
      </div>

      {/* Model Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-slate-200/80 dark:border-zinc-800/80 text-slate-500 dark:text-zinc-400 text-[11px]">
              <th className="pb-2.5 font-medium">模型名称 / 接入工具</th>
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
              const cacheRead = (m.cr || 0) + (m.cached || 0);
              const barWidth = Math.min(100, Math.max(3, ((m.tokens || 0) / maxTokens) * 100));

              return (
                <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-zinc-800/20 transition-colors">
                  <td className="py-2.5 font-sans">
                    <div className="flex items-center gap-2">
                      <span className="w-4 text-slate-400 dark:text-zinc-500 text-[11px] font-mono">{idx + 1}.</span>
                      <span className="font-semibold text-slate-800 dark:text-zinc-200">{m.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-zinc-800 text-slate-500 dark:text-zinc-400 uppercase font-mono">
                        {m.tool}
                      </span>
                    </div>
                  </td>
                  <td className="py-2.5 text-right font-bold text-slate-900 dark:text-zinc-100">
                    {formatTokens(m.tokens || 0)}
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
                    ${(m.cost || 0).toFixed(2)}
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
      {filteredModels.length > 10 && (
        <div className="mt-3 pt-3 border-t border-slate-100 dark:border-zinc-800/60 flex justify-center">
          <button
            onClick={() => setShowAll(!showAll)}
            className="text-xs text-slate-500 dark:text-zinc-400 hover:text-slate-800 dark:hover:text-zinc-200 flex items-center gap-1.5 py-1 px-3 rounded-lg hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors font-medium"
          >
            {showAll ? (
              <>
                <span>收起部分模型</span>
                <ChevronUp className="w-3.5 h-3.5" />
              </>
            ) : (
              <>
                <span>展开全部 {filteredModels.length} 个模型明细</span>
                <ChevronDown className="w-3.5 h-3.5" />
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
