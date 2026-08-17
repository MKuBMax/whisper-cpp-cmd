#!/bin/bash
# 重启 WhisperCppCmd.app，加载最新源码（py2app alias 模式：.app 引用源码，重启即生效）。
# 用法：bash restart_app.sh [app_path]
set -uo pipefail
APP="${1:-/Applications/WhisperCppCmd.app}"
MATCH="${APP}/Contents/MacOS/WhisperCppCmd"

# 终止运行中的进程（按完整路径精确匹配，避免误杀 build 脚本）
if pgrep -f "$MATCH" >/dev/null 2>&1; then
  pkill -f "$MATCH" 2>/dev/null || true
  for _ in $(seq 1 15); do
    pgrep -f "$MATCH" >/dev/null 2>&1 || break
    sleep 0.2
  done
  if pgrep -f "$MATCH" >/dev/null 2>&1; then
    echo "⚠️  进程未退出，可能需手动 kill：$(pgrep -f "$MATCH")" >&2
    exit 1
  fi
fi

open "$APP"
echo "✅ 已重启 ${APP}（最新源码生效）"
