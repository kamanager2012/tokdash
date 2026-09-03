# ⏱ TokDash (Ubuntu / Linux AI Token & Cost Monitor)

> 原项目灵感：[cclank/tokei](https://github.com/cclank/tokei)（原版仅支持 macOS 菜单栏）  
> 本版本为专为 **Ubuntu / Linux** 开发的独立桌面端，采用与 `cc-switch` 一致的高质感深色卡片窗口与系统托盘。

---

## 🌟 核心特性

- **跨平台窗口质感**：沉浸式深色模式、无边框现代窗口、悬停数据卡片与走势图，交互风格与 `cc-switch` 完全看齐。
- **系统托盘（Tray）常驻**：在 Ubuntu 桌面顶部/底部托盘常驻，支持点击唤醒、最小化和隐藏到后台。
- **多维度时间统计**：支持切换 **今日 / 昨日 / 本周 / 本月 / 今年 / 全部** 快速对比用量。
- **全工具覆盖（本地零侵入读取）**：
  - **Codex CLI**（`~/.codex/`）
  - **Claude Code**（`~/.claude/`）
  - **Gemini / Antigravity CLI**（本地服务与会话）
  - **Pi Coding Agent CLI**（`~/.pi/`）
  - **Grok Build / Bot**
  - **Kimi Code**（`~/.kimi-code/`）
  - **OpenCode**（`~/.opencode/`）
  - **DeepSeek Harness**（`~/.dsh/`）
- **核心数据看板**：
  - 💰 **总 API 花费**（匹配 OpenRouter 317 个模型实时价目库 `pricing.json`）
  - ⚡ **总吞吐 Token**（区分 Prompt 输入、补全输出与缓存读取）
  - 🎯 **提示词缓存命中率**（Cache Hit %）
  - 📈 **近两周单日费用走势柱状图**（悬停显示工具明细）
  - 🤖 **Top 核心模型用量排行与占比**

---

## 🚀 启动方式

### 方式 1：终端一键启动
```bash
/home/jamesoldman/tokdash/start.sh
```

### 方式 2：应用菜单点击
已自动生成桌面快捷入口，直接在 Ubuntu 的 **应用菜单（Applications）** 搜索 **TokDash** 打开即可。

### 开发与构建
```bash
cd /home/jamesoldman/tokdash
pnpm run build   # 重新构建前端静态资源
pnpm start       # 启动桌面端
```
