#!/bin/bash
# GPT-SoVITS 安装器 · 一键启动（Mac/Linux）
cd "$(dirname "$0")"

echo ""
echo "  GPT-SoVITS 安装器启动中..."
echo ""

# 检查 Node.js
if ! command -v node &>/dev/null; then
  echo "  [✗] 未找到 Node.js，请先安装: https://nodejs.org"
  echo "      或直接用浏览器打开 index.html（模拟模式）"
  open index.html 2>/dev/null || xdg-open index.html 2>/dev/null || echo "  请手动打开 index.html"
  exit 1
fi

echo "  [✓] Node.js $(node --version)"
echo "  [→] 启动后端服务: http://localhost:3333"
echo ""
node server.js
