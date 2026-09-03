import React, { useState } from 'react';
import { X, RefreshCw, CheckCircle2, Shield, HardDrive } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  pricingMeta?: { updated_at?: string; count?: number };
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  pricingMeta,
}) => {
  const [updating, setUpdating] = useState(false);
  const [updateMsg, setUpdateMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleUpdatePrices = async () => {
    setUpdating(true);
    setUpdateMsg(null);
    try {
      await (window as any).tokdash?.updatePrices();
      setUpdateMsg('价格表已更新至最新！');
    } catch (e: any) {
      setUpdateMsg('更新失败: ' + (e?.message || '网络错误'));
    } finally {
      setUpdating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-white dark:bg-[#181822] border border-slate-200 dark:border-zinc-800 rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150 transition-colors">
        <div className="flex items-center justify-between p-4 border-b border-slate-200 dark:border-zinc-800">
          <h3 className="font-semibold text-sm text-slate-800 dark:text-zinc-100 flex items-center gap-2">
            <span>Tokei 知度 - 设置与元数据</span>
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-100 dark:hover:bg-zinc-800 text-slate-400 hover:text-slate-600 dark:hover:text-zinc-200"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4 text-xs text-slate-600 dark:text-zinc-300">
          {/* Pricing database info */}
          <div className="bg-slate-50 dark:bg-[#121216] p-3.5 rounded-xl border border-slate-200 dark:border-zinc-800/80 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-slate-800 dark:text-zinc-200">模型价格表 (OpenRouter 定价)</span>
              <span className="text-[11px] px-2 py-0.5 rounded bg-sky-500/10 text-sky-600 dark:text-sky-400 font-mono">
                {pricingMeta?.count || 317} 个模型
              </span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-zinc-400">
              用于精确推算 Claude、Codex、Gemini、DeepSeek 等模型的实际 Token 成本。
            </p>
            {pricingMeta?.updated_at && (
              <div className="text-[10px] text-slate-400 dark:text-zinc-500">
                更新时间: {pricingMeta.updated_at}
              </div>
            )}
            <div className="pt-2">
              <button
                onClick={handleUpdatePrices}
                disabled={updating}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-600 text-white font-medium transition-colors disabled:opacity-50 shadow-sm"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${updating ? 'animate-spin' : ''}`} />
                <span>{updating ? '正在联网同步价格...' : '联网同步最新费率'}</span>
              </button>
            </div>
            {updateMsg && (
              <div className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1 mt-1 font-medium">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{updateMsg}</span>
              </div>
            )}
          </div>

          {/* Privacy & Architecture info */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-slate-700 dark:text-zinc-200 font-medium">
              <Shield className="w-4 h-4 text-emerald-500" />
              <span>纯本地架构与防双重计费</span>
            </div>
            <p className="text-slate-500 dark:text-zinc-400 leading-relaxed text-[11px]">
              TokDash 绝不依赖任何私有代理或中间层，纯粹只读取系统本地存储目录（如 ~/.codex/、~/.claude/、~/.cursor/、~/.codebuddy/），数据真实可溯源。
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-slate-700 dark:text-zinc-200 font-medium">
              <HardDrive className="w-4 h-4 text-purple-500" />
              <span>数据存储路径</span>
            </div>
            <div className="bg-slate-100 dark:bg-zinc-900/60 p-2.5 rounded-lg font-mono text-[10px] text-slate-600 dark:text-zinc-400 break-all space-y-1">
              <div>~/.config/tokdash/scan_cache.json (增量缓存)</div>
              <div>pricing_overrides.json (项目根目录下费率与别名覆写)</div>
            </div>
          </div>
        </div>

        <div className="p-4 bg-slate-50 dark:bg-[#14141c] border-t border-slate-200 dark:border-zinc-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-slate-200 dark:bg-zinc-800 hover:bg-slate-300 dark:hover:bg-zinc-700 text-slate-700 dark:text-zinc-200 transition-colors text-xs font-medium"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  );
};
