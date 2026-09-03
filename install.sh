#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==> 正在安装 TokDash Linux 桌面端..."
echo "==> 安装目录: $DIR"

# 1. 检查 Python3
if ! command -v python3 &>/dev/null; then
    echo "错误: 未找到 python3，请先安装: sudo apt install python3"
    exit 1
fi

# 2. 检查 Node.js
if ! command -v node &>/dev/null; then
    echo "错误: 未找到 node，请先安装 Node.js (>=18)"
    exit 1
fi

cd "$DIR"

# 3. 安装依赖（如果 node_modules 不存在）
if [ ! -d "node_modules" ]; then
    echo "==> 正在安装前端与 Electron 依赖..."
    npm install
fi

echo "==> 正在构建前端界面..."
./node_modules/.bin/vite build

# 4. 创建可执行启动脚本
chmod +x "$DIR/start.sh"

# 5. 注册 Ubuntu 桌面应用快捷方式
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"

cat << DESKTOP_EOF > "$APP_DIR/tokdash.desktop"
[Desktop Entry]
Name=TokDash
Comment=AI 编程用量与成本监控桌面端 (Ubuntu/Linux)
Exec=$DIR/start.sh
Icon=$DIR/icon.png
Terminal=false
Type=Application
Categories=Development;Utility;
DESKTOP_EOF

chmod +x "$APP_DIR/tokdash.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true

# 6. 配置开机自启（可选，支持 --autostart 参数）
ENABLE_AUTOSTART=false
for arg in "$@"; do
    if [ "$arg" == "--autostart" ]; then
        ENABLE_AUTOSTART=true
    fi
done

AUTOSTART_DIR="$HOME/.config/autostart"
if [ "$ENABLE_AUTOSTART" = true ]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$APP_DIR/tokdash.desktop" "$AUTOSTART_DIR/tokdash.desktop"
    AUTOSTART_STATUS="已启用 (~/.config/autostart/tokdash.desktop)"
else
    AUTOSTART_STATUS="未启用 (如需开启，请带参数运行: ./install.sh --autostart)"
fi

echo "=================================================="
echo "✅ TokDash 安装成功！"
echo "  - 启动方式 1: 在 Ubuntu 应用中心搜索 'TokDash' 点击运行"
echo "  - 启动方式 2: 终端直接运行 $DIR/start.sh"
echo "  - 开机自启: $AUTOSTART_STATUS"
echo "=================================================="
