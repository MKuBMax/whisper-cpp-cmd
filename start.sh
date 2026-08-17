#!/bin/bash
# 语音输入工具启动脚本

set -euo pipefail

PROJECT_DIR="/Users/mkbm/work/app/whisper-cpp-cmd"
CONDA_BIN="/Users/mkbm/miniconda3/bin/conda"
ENV_NAME="voice-input"

cd "$PROJECT_DIR"

echo "启动语音输入工具..."
exec "$CONDA_BIN" run -n "$ENV_NAME" python3 main.py
