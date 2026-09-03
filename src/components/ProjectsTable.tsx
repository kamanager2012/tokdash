import React from 'react';
import { FolderGit2 } from 'lucide-react';
import { formatTokens } from '../utils';
import type { ProjectRecord } from '../types';

interface ProjectsTableProps {
  projects: ProjectRecord[];
}


export const ProjectsTable: React.FC<ProjectsTableProps> = ({ projects }) => {
  if (!projects || projects.length === 0) return null;

  return (
    <div className="bg-white dark:bg-[#181820] border border-slate-200 dark:border-zinc-800/80 rounded-xl p-4 shadow-sm mb-6 transition-colors">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-sky-500/10 text-sky-500">
            <FolderGit2 className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-zinc-100">代码工程项目明细 (Projects)</h3>
            <p className="text-[11px] text-slate-500 dark:text-zinc-400">各个代码仓库中 AI 辅助编码的累计消耗</p>
          </div>
        </div>
        <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 dark:bg-zinc-800 text-slate-600 dark:text-zinc-400">
          共 {projects.length} 个本地活跃工程
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-slate-200/80 dark:border-zinc-800/80 text-slate-500 dark:text-zinc-400 text-[11px]">
              <th className="pb-2.5 font-medium">工程目录</th>
              <th className="pb-2.5 font-medium">涉及工具</th>
              <th className="pb-2.5 font-medium">主用模型</th>
              <th className="pb-2.5 font-medium text-right">会话场次</th>
              <th className="pb-2.5 font-medium text-right">总 Token</th>
              <th className="pb-2.5 font-medium text-right">产生花费</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-zinc-800/40">
            {projects.map((p, idx) => (
              <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-zinc-800/20 transition-colors">
                <td className="py-2.5 font-mono">
                  <div className="font-semibold text-slate-800 dark:text-zinc-100">{p.name}</div>
                  <div className="text-[10px] text-slate-400 dark:text-zinc-400 truncate max-w-[280px]" title={p.path}>
                    {p.path}
                  </div>
                </td>
                <td className="py-2.5">
                  <div className="flex flex-wrap gap-1">
                    {(p.tools || []).map((t, ti) => (
                      <span
                        key={ti}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-zinc-800 text-slate-600 dark:text-zinc-300 font-medium"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="py-2.5 text-slate-700 dark:text-zinc-300">
                  {p.top_model ? (
                    <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-zinc-800/60 text-slate-700 dark:text-zinc-300 border border-slate-200 dark:border-zinc-700/40">
                      {p.top_model}
                    </span>
                  ) : (
                    <span className="text-slate-400 dark:text-zinc-400">-</span>
                  )}
                </td>
                <td className="py-2.5 text-right font-mono text-slate-700 dark:text-zinc-300">
                  {p.sessions || 0}
                </td>
                <td className="py-2.5 text-right font-mono text-slate-700 dark:text-zinc-300">
                  {formatTokens(p.tokens || 0)}
                </td>
                <td className="py-2.5 text-right font-mono font-semibold text-emerald-600 dark:text-emerald-400">
                  ${(p.cost || 0).toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
