#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==> 正在安装 Cognitally (原 TokDash) Linux 桌面端与 CLI..."
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
    if command -v pnpm >/dev/null 2>&1; then
        pnpm install
    elif command -v corepack >/dev/null 2>&1; then
        corepack enable >/dev/null 2>&1 || true
        corepack prepare pnpm@9.15.9 --activate >/dev/null 2>&1 || true
        if command -v pnpm >/dev/null 2>&1; then
            pnpm install
        else
            npm install
        fi
    else
        npm install
    fi
fi

echo "==> 正在构建前端界面..."
./node_modules/.bin/vite build

# 4. 创建可执行启动脚本与 CLI 软链接
chmod +x "$DIR/start.sh"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
# Bake absolute install path into the CLI shim (no hardcoded user home).
cat << CLI_EOF > "$BIN_DIR/cognitally"
#!/usr/bin/env bash
exec python3 "$DIR/usage.30s.py" "\$@"
CLI_EOF
chmod +x "$BIN_DIR/cognitally"
ln -sf "$BIN_DIR/cognitally" "$BIN_DIR/tokdash"

# 5. 注册 Ubuntu 桌面应用快捷方式
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"

cat << DESKTOP_EOF > "$APP_DIR/cognitally.desktop"
[Desktop Entry]
Name=Cognitally
Comment=AI 编程用量与成本监控桌面端 (Ubuntu/Linux)
Exec=$DIR/start.sh
Icon=$DIR/icon.png
Terminal=false
Type=Application
Categories=Development;Utility;
DESKTOP_EOF

chmod +x "$APP_DIR/cognitally.desktop"
ln -sf "$APP_DIR/cognitally.desktop" "$APP_DIR/tokdash.desktop"
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
    cp "$APP_DIR/cognitally.desktop" "$AUTOSTART_DIR/cognitally.desktop"
    AUTOSTART_STATUS="已启用 (~/.config/autostart/cognitally.desktop)"
else
    AUTOSTART_STATUS="未启用 (如需开启，请带参数运行: ./install.sh --autostart)"
fi

echo "=================================================="
echo "✅ Cognitally 安装成功！"
echo "  - CLI 工具: 已注册 'cognitally' (兼容别名 'tokdash')"
echo "  - 启动桌面端: 在 Ubuntu 应用中心搜索 'Cognitally' 或运行 $DIR/start.sh"
echo "  - MCP 服务: cognitally --mcp"
echo "  - 诊断检查: cognitally --doctor"
echo "  - 开机自启: $AUTOSTART_STATUS"
echo "=================================================="
