import React from 'react';
import { Period } from '../types';
import { Clock, RefreshCw, Settings, Minus, Square, X, Sun, Moon } from 'lucide-react';

interface HeaderProps {
  period: Period;
  setPeriod: (p: Period) => void;
  onRefresh: () => void;
  loading: boolean;
  onOpenSettings: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}

const currentMonth = new Date().getMonth() + 1;
const PERIOD_LABELS: Partial<Record<Period, string>> = {
  today: '今日',
  yesterday: '昨日',
  '7d': '近 7 天',
  '30d': '近 30 天',
  month: `本月 (${currentMonth}月)`,
  all: '全部',
};

export const Header: React.FC<HeaderProps> = ({
  period,
  setPeriod,
  onRefresh,
  loading,
  onOpenSettings,
  theme,
  onToggleTheme,
}) => {
  const handleMinimize = () => (window as any).tokdash?.minimizeWindow();
  const handleMaximize = () => (window as any).tokdash?.toggleMaximize();
  const handleClose = () => (window as any).tokdash?.closeWindow();

  return (
    <header className="h-14 bg-white/80 dark:bg-[#14141a]/90 backdrop-blur-md border-b border-slate-200 dark:border-zinc-800/80 flex items-center justify-between px-4 select-none app-drag sticky top-0 z-50 transition-colors">
      {/* Left branding */}
      <div className="flex items-center gap-3 app-no-drag">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-sky-500 to-indigo-500 flex items-center justify-center shadow-md shadow-sky-500/20">
          <Clock className="w-4 h-4 text-white" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm tracking-wide text-slate-800 dark:text-zinc-100">
              TokDash
            </span>
            <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20">
              Linux
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-zinc-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>本地日志实时监控</span>
          </div>
        </div>
      </div>

      {/* Center Period Tabs */}
      <div className="flex items-center bg-slate-100 dark:bg-[#1e1e26] p-1 rounded-lg border border-slate-200/80 dark:border-zinc-800 app-no-drag shadow-inner">
        {(Object.keys(PERIOD_LABELS) as Period[]).map((key) => (
          <button
            key={key}
            onClick={() => setPeriod(key)}
            className={`px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 ${
              period === key
                ? 'bg-sky-500 text-white shadow-md shadow-sky-500/25'
                : 'text-slate-600 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 hover:bg-slate-200/60 dark:hover:bg-zinc-800/50'
            }`}
          >
            {PERIOD_LABELS[key]}
          </button>
        ))}
      </div>

      {/* Right Actions & Window Controls */}
      <div className="flex items-center gap-2 app-no-drag">
        {/* Theme switch button */}
        <button
          onClick={onToggleTheme}
          title={theme === 'dark' ? '切换为浅色主题 (Light Mode)' : '切换为深色主题 (Dark Mode)'}
          className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-100 transition-colors"
        >
          {theme === 'dark' ? (
            <Sun className="w-4 h-4 text-amber-400 hover:text-amber-300" />
          ) : (
            <Moon className="w-4 h-4 text-indigo-500 hover:text-indigo-600" />
          )}
        </button>

        <button
          onClick={onRefresh}
          disabled={loading}
          title="刷新数据"
          className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-100 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-sky-500' : ''}`} />
        </button>
        <button
          onClick={onOpenSettings}
          title="设置与价格表"
          className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-100 transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>

        <div className="h-4 w-[1px] bg-slate-200 dark:bg-zinc-800 mx-1"></div>

        {/* Window controls */}
        <button
          onClick={handleMinimize}
          className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 transition-colors"
        >
          <Minus className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleMaximize}
          className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 transition-colors"
        >
          <Square className="w-3 h-3" />
        </button>
        <button
          onClick={handleClose}
          className="p-1.5 rounded hover:bg-red-500/10 text-slate-500 dark:text-zinc-400 hover:text-red-500 transition-colors"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </header>
  );
};
