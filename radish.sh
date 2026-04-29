#!/bin/bash
# RadishWeixinBot 启动脚本 - 在任意目录下执行，模型目录即为执行目录
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate" 2>/dev/null || true
# 切换到当前执行目录（模型目录）
cd "$(pwd)"
# 启动 console.py，不传 --model-dir 参数
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/llmServer/console.py" "$@"
