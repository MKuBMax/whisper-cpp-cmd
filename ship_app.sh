#!/bin/bash
# 提交时部署：重新打包（build_app.sh）+ 替换 /Applications/WhisperCppCmd.app + 重启。
# 用法：bash ship_app.sh
set -euo pipefail
PROJECT_DIR="/Users/mkbm/work/app/whisper-cpp-cmd"
APP_NAME="WhisperCppCmd"
INSTALLED="/Applications/${APP_NAME}.app"
cd "$PROJECT_DIR"

echo "==> 退出运行中的进程"
pkill -f "${INSTALLED}/Contents/MacOS/WhisperCppCmd" 2>/dev/null || true
pkill -f "${PROJECT_DIR}/${APP_NAME}.app/Contents/MacOS/WhisperCppCmd" 2>/dev/null || true
sleep 1

echo "==> 重新打包 (build_app.sh)"
bash "$PROJECT_DIR/build_app.sh"

echo "==> 替换 ${INSTALLED}"
rm -rf "$INSTALLED"
ditto "${PROJECT_DIR}/${APP_NAME}.app" "$INSTALLED"

echo "==> 启动"
open "$INSTALLED"
echo "✅ 已打包并部署到 ${INSTALLED}"
