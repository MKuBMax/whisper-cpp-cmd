#!/bin/bash
# 开发模式：直接运行源码，不替换 /Applications/WhisperCppCmd.app。
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv-arm64/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "❌ 找不到项目 Python：$PYTHON" >&2
  exit 1
fi
cd "$PROJECT_DIR"
export WHISPER_CPP_CMD_DEV=1
exec "$PYTHON" main.py "$@"
