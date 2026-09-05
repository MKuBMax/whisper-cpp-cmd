#!/bin/bash
# 开发模式：构建并启动本地 DEV 启动器 App（py2app alias，引用源码）。
# 不替换 /Applications/WhisperCppCmd.app；正式发布仍走 ship_app.sh。
# DEV 与正式版共用同一套数据（config.json、模型、历史），同时只跑一个：
# 启动 DEV 前先停掉正式版和旧 DEV。
# 用法：bash run_dev_app.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_APP_NAME="WhisperCppCmdDev"
DEV_APP="$PROJECT_DIR/build/dev/${DEV_APP_NAME}.app"
DEV_EXECUTABLE="$DEV_APP/Contents/MacOS/$DEV_APP_NAME"
FORMAL_MATCH="/Applications/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd"
ARM64_PYTHON="${WHISPER_CPP_CMD_PYTHON:-$PROJECT_DIR/.venv-arm64/bin/python}"
BUILD_DIR="$PROJECT_DIR/.py2app-build-dev"
DIST_DIR="$PROJECT_DIR/.py2app-dist-dev"

stop_match() {
  local match="$1"
  local pids=""
  pids="$(pgrep -f "$match" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 0
  fi
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 25); do
    pids="$(pgrep -f "$match" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
      return 0
    fi
    sleep 0.2
  done
  echo "⚠️  进程未响应 SIGTERM，改用 SIGKILL：$match（PID: $pids）" >&2
  kill -KILL $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    pids="$(pgrep -f "$match" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
      return 0
    fi
    sleep 0.2
  done
  echo "❌ 进程仍未退出：$match（PID: $pids）" >&2
  return 1
}

echo "==> 停止正式版和旧 DEV（DEV 与正式版同时只跑一个）"
stop_match "$FORMAL_MATCH" || exit 1
stop_match "$DEV_EXECUTABLE" || exit 1

if [ ! -x "$ARM64_PYTHON" ]; then
  echo "❌ 找不到项目 Python：$ARM64_PYTHON" >&2
  exit 1
fi

# alias 构建只写 build/dev，不碰 release/ 和 /Applications。
rm -rf "$BUILD_DIR" "$DIST_DIR" "$DEV_APP"
cd "$PROJECT_DIR"
"$ARM64_PYTHON" setup_dev.py py2app -A \
  --dist-dir "$DIST_DIR" \
  --bdist-base "$BUILD_DIR" >/tmp/whisper_dev_app_build.log 2>&1 || {
  tail -30 /tmp/whisper_dev_app_build.log >&2
  exit 1
}
ditto "$DIST_DIR/$DEV_APP_NAME.app" "$DEV_APP"

BUNDLE_ALIAS="$(/usr/libexec/PlistBuddy -c 'Print :PyOptions:alias' \
  "$DEV_APP/Contents/Info.plist" 2>/dev/null || true)"
if [ "$BUNDLE_ALIAS" != "true" ]; then
  echo "❌ DEV App 不是 alias 模式（PyOptions:alias=$BUNDLE_ALIAS），拒绝启动。" >&2
  exit 1
fi

echo "==> 启动 DEV App：$DEV_APP"
open "$DEV_APP"
echo "✅ DEV App 已启动（alias 引用源码，改完代码重跑本脚本即生效）"
echo "   首次运行需在系统设置中为 WhisperCppCmdDev 允许麦克风和辅助功能。"
