#!/bin/bash
# MiniMax API 独立后端启动脚本
# 使用方式: bash start.sh

set -e

BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$BACKEND_DIR")"
SDK_DIR="$PROJECT_DIR/python-sdk"

echo "============================================"
echo "  MiniMax API 独立后端启动脚本"
echo "============================================"

# 1. 检查 .env
if [ ! -f "$BACKEND_DIR/.env" ]; then
    if [ -n "$MINIMAX_API_KEY" ]; then
        echo "✅ 检测到环境变量 MINIMAX_API_KEY"
    else
        echo ""
        echo "⚠️  未检测到 MINIMAX_API_KEY"
        echo "   请复制 .env.example 为 .env 并填入你的 API Key"
        echo "   或设置环境变量: export MINIMAX_API_KEY=your_key"
        echo ""
        echo "   API Key 获取地址: https://platform.minimaxi.com"
        echo ""
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
        echo "   已创建 .env 模板，请编辑并填入你的 API Key"
        exit 1
    fi
fi

# 2. 创建 Python 虚拟环境
VENV_DIR="$BACKEND_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi

# 3. 安装依赖
echo "📦 安装依赖..."
source "$VENV_DIR/bin/activate"
pip install -q --upgrade pip
pip install -q -e "$SDK_DIR"
pip install -q fastapi uvicorn[standard] python-dotenv

# 4. 启动服务
echo ""
echo "🚀 启动后端服务..."
echo "   地址: http://localhost:${PORT:-8000}"
echo "   测试页: http://localhost:${PORT:-8000}"
echo "   API 文档: http://localhost:${PORT:-8000}/docs"
echo ""
cd "$BACKEND_DIR"
python main.py
