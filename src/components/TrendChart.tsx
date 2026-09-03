import React, { useState } from 'react';
import { DailyCostRecord } from '../types';
import { TrendingUp, Calendar } from 'lucide-react';
import { TOOL_DISPLAY_NAMES } from '../utils';


interface TrendChartProps {
  data: DailyCostRecord[];
}

export const TrendChart: React.FC<TrendChartProps> = ({ data }) => {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  // Take the last 14 days or available items
  const recentDays = (data || []).slice(-14);
  const maxCost = Math.max(...recentDays.map((d) => d.total || 0), 10);

  if (recentDays.length === 0) {
    return null;
  }

  const activeItem = hoverIndex !== null ? recentDays[hoverIndex] : recentDays[recentDays.length - 1];

  return (
    <div className="bg-white dark:bg-[#181820] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 mb-6 shadow-sm transition-colors">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-sky-500/10 text-sky-500">
            <TrendingUp className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100">每日费用走势（近{recentDays.length}天）</h3>
            <p className="text-[11px] text-slate-500 dark:text-zinc-400">悬停查看单日各工具消耗细分</p>
          </div>
        </div>

        {activeItem && (
          <div className="flex items-center gap-3 bg-slate-50 dark:bg-zinc-900/90 border border-slate-200 dark:border-zinc-800 px-3 py-1.5 rounded-lg text-xs">
            <div className="flex items-center gap-1.5 text-slate-600 dark:text-zinc-400">
              <Calendar className="w-3.5 h-3.5 text-sky-500" />
              <span>{activeItem.date}</span>
            </div>
            <div className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
              ${activeItem.total?.toFixed(2)}
            </div>
            {/* Dynamic tool cost breakdown: iterate over all non-zero tool entries in record */}
            {Object.entries(activeItem)
              .filter(([k, v]) => !['date', 'total', 'tokens'].includes(k) && typeof v === 'number' && v > 0)
              .filter(([k, v]) => !(k === 'grok' && activeItem['grokbuild'] === v))
              .sort(([, a], [, b]) => (b as number) - (a as number))
              .map(([key, val]) => (
                <span key={key} className="text-[11px] text-slate-500 dark:text-zinc-400">
                  {TOOL_DISPLAY_NAMES[key] ?? key}: ${(val as number).toFixed(2)}
                </span>
              ))}
          </div>
        )}
      </div>

      {/* SVG Bar / Area Chart */}
      <div className="h-44 w-full flex items-end gap-2 pt-6 pb-2 px-2 border-b border-slate-200/80 dark:border-zinc-800/60">
        {recentDays.map((item, idx) => {
          const heightPercent = Math.min(100, Math.max(8, ((item.total || 0) / maxCost) * 100));
          const isHovered = hoverIndex === idx;

          return (
            <div
              key={item.date}
              className="flex-1 h-full flex flex-col justify-end items-center group cursor-pointer relative"
              onMouseEnter={() => setHoverIndex(idx)}
              onMouseLeave={() => setHoverIndex(null)}
            >
              {/* Cost label on hover */}
              {isHovered && (
                <div className="absolute -top-6 bg-slate-900 dark:bg-zinc-800 text-white text-[10px] font-mono px-1.5 py-0.5 rounded shadow-lg whitespace-nowrap z-10 animate-in fade-in zoom-in-95 duration-100">
                  ${item.total?.toFixed(2)}
                </div>
              )}

              {/* Bar */}
              <div
                className={`w-full rounded-t-md transition-all duration-200 ${
                  isHovered
                    ? 'bg-sky-500 shadow-lg shadow-sky-500/30'
                    : 'bg-slate-200 dark:bg-zinc-800 hover:bg-sky-400/80 dark:hover:bg-zinc-700'
                }`}
                style={{ height: `${heightPercent}%` }}
              />

              {/* Day label */}
              <span className="text-[10px] text-slate-400 dark:text-zinc-500 mt-2 font-mono group-hover:text-slate-800 dark:group-hover:text-zinc-300 transition-colors">
                {item.date.slice(5)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
