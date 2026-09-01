#!/bin/bash
# 重启 standalone WhisperCppCmd.app。
# 默认启动 /Applications 中由 ship_app.sh 替换安装的包。
# 用法：bash restart_app.sh [app_path]
set -uo pipefail
INSTALLED_APP="/Applications/WhisperCppCmd.app"
APP="${1:-$INSTALLED_APP}"
MATCH="${APP}/Contents/MacOS/WhisperCppCmd"

if [ ! -x "$MATCH" ]; then
  echo "❌ 找不到可启动的 App：$APP" >&2
  echo "   请先运行：bash ship_app.sh" >&2
  exit 1
fi

BUNDLE_ALIAS="$(/usr/libexec/PlistBuddy -c 'Print :PyOptions:alias' \
  "$APP/Contents/Info.plist" 2>/dev/null || true)"
if [ "$BUNDLE_ALIAS" != "false" ]; then
  echo "❌ 拒绝启动 alias App；本机流程只允许 standalone 包：$APP" >&2
  echo "   请运行：bash ship_app.sh" >&2
  exit 1
fi

stop_app() {
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
  echo "⚠️  进程未响应 SIGTERM，改用 SIGKILL：$pids" >&2
  kill -KILL $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    pids="$(pgrep -f "$match" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
      return 0
    fi
    sleep 0.2
  done
  echo "❌ 进程仍未退出，可能需手动处理：$pids" >&2
  return 1
}

if ! stop_app "$MATCH"; then
  exit 1
fi

if ! open "$APP"; then
  echo "❌ 无法启动 $APP" >&2
  exit 1
fi
echo "✅ 已重启 standalone App：$APP"
