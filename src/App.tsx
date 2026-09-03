import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Period, UsageReport, DailyCostRecord, TopModelRecord, ProjectRecord } from './types';
import { Header } from './components/Header';
import { OverviewCards, isToolKey } from './components/OverviewCards';
import { calculateTotalTokens } from './utils';
import { QuotaCards } from './components/QuotaCards';
import { TrendChart } from './components/TrendChart';
import { ToolCardList } from './components/ToolCardList';
import { ModelBreakdown } from './components/ModelBreakdown';
import { ProjectsTable } from './components/ProjectsTable';
import { SettingsModal } from './components/SettingsModal';
import { Loader2, AlertTriangle, X } from 'lucide-react';

export const App: React.FC = () => {
  const [period, setPeriod] = useState<Period>('today');
  const [usage, setUsage] = useState<UsageReport>({});
  const [dailyCosts, setDailyCosts] = useState<DailyCostRecord[]>([]);
  const [topModels, setTopModels] = useState<TopModelRecord[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [dismissErrors, setDismissErrors] = useState(false);

  // Theme support: dark / light
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('tokdash-theme') as 'dark' | 'light') || 'dark';
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
      root.classList.remove('light');
    } else {
      root.classList.remove('dark');
      root.classList.add('light');
    }
    localStorage.setItem('tokdash-theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      if ((window as any).tokdash) {
        const [usageRes, dailyRes, projRes] = await Promise.all([
          (window as any).tokdash.fetchUsage(),
          (window as any).tokdash.fetchDailyCosts(),
          (window as any).tokdash.fetchProjects(),
        ]);
        if (usageRes) setUsage(usageRes);
        if (projRes && Array.isArray(projRes)) setProjects(projRes);
        if (dailyRes) {
          if (Array.isArray(dailyRes)) {
            setDailyCosts(dailyRes);
          } else if (dailyRes.daily) {
            setDailyCosts(dailyRes.daily);
            if (dailyRes.models) setTopModels(dailyRes.models);
          }
        }
      }
    } catch (err) {
      console.error('Failed to load tokdash data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    // Auto-refresh every 30 seconds
    const timer = setInterval(loadData, 30_000);
    return () => clearInterval(timer);
  }, [loadData]);

  // Dynamically compute model usage matching the CURRENT period across all scanned tools
  const currentPeriodModels = useMemo(() => {
    const map: Record<string, TopModelRecord> = {};
    Object.entries(usage).forEach(([toolKey, toolVal]) => {
      if (!isToolKey(toolKey, toolVal)) return;
      const r = toolVal.ranges?.[period];
      if (!r || !r.models) return;
      r.models.forEach((m: any) => {
        const cr = (m.cr || 0) + (m.cached || 0);
        const fullIn = (m.in || 0) + cr;
        const out = m.out || 0;
        const reason = m.reason || 0;
        // Accurate token accounting avoiding Codex double-counting
        const tokens = calculateTotalTokens(toolKey, fullIn, out, reason);
        const cost = m.cost || 0;
        if (tokens === 0 && cost === 0) return;

        const name = m.name || m.model_id;
        const key = `${name} (${toolKey})`;
        if (!map[key]) {
          map[key] = {
            name: name,
            cost: cost,
            in: fullIn,
            out: out,
            cr: cr,
            cw: m.cw || 0,
            reason: reason,
            tokens: tokens,
            tool: toolKey,
            cost_per_k: 0,
            out_ratio: 0,
          };
        } else {
          map[key].cost += cost;
          map[key].in += fullIn;
          map[key].out += out;
          map[key].cr += cr;
          map[key].tokens += tokens;
        }
      });
    });
    const list = Object.values(map);
    if (list.length > 0) {
      return list.sort((a, b) => (b.tokens || 0) - (a.tokens || 0));
    }
    return topModels;
  }, [usage, period, topModels]);

  const pricingMeta = usage._pricing;

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-[#f4f5f8] dark:bg-[#0f0f13] text-slate-800 dark:text-zinc-100 font-sans transition-colors duration-200">
      <Header
        period={period}
        setPeriod={setPeriod}
        onRefresh={loadData}
        loading={loading}
        onOpenSettings={() => setIsSettingsOpen(true)}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto px-6 py-5">
        {loading && Object.keys(usage).length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-400 dark:text-zinc-400 gap-3">
            <Loader2 className="w-8 h-8 animate-spin text-sky-500" />
            <p className="text-xs">正在扫描本机各 AI 编程工具会话日志...</p>
          </div>
        ) : (
          <div className="max-w-6xl mx-auto space-y-6">
            {/* Parser Failures & Telemetry Errors Notification */}
            {usage._errors && Object.keys(usage._errors).length > 0 && !dismissErrors && (
              <div className="bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400 px-4 py-2.5 rounded-xl text-xs flex items-center justify-between shadow-sm">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 text-amber-500" />
                  <span>
                    部分工具采集异常（已降级为不可用，非零用量）：
                    <strong> {Object.keys(usage._errors).join(', ')}</strong>
                  </span>
                </div>
                <button
                  onClick={() => setDismissErrors(true)}
                  className="hover:opacity-75 p-1 text-slate-500 dark:text-zinc-400"
                  title="关闭提示"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
            <QuotaCards usage={usage} />
            <OverviewCards usage={usage} period={period} />
            <TrendChart data={dailyCosts} />
            <ToolCardList usage={usage} period={period} />
            <ModelBreakdown models={currentPeriodModels} />
            <ProjectsTable projects={projects} />
          </div>
        )}
      </main>

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        pricingMeta={pricingMeta}
      />
    </div>
  );
};

export default App;
