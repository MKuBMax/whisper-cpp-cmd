#!/bin/bash
# 本机/提交时部署：构建 standalone 分发包 + 替换 /Applications/WhisperCppCmd.app + 重启。
# 用法：bash ship_app.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WhisperCppCmd"
INSTALLED="/Applications/${APP_NAME}.app"
PACKAGE_APP="$PROJECT_DIR/release/${APP_NAME}-macOS-arm64/${APP_NAME}.app"
STAGING="/Applications/.${APP_NAME}.app.installing.$$"
cd "$PROJECT_DIR"

cleanup() {
  if [ -d "$STAGING" ]; then
    rm -rf "$STAGING"
  fi
}
trap cleanup EXIT

stop_app() {
  local match="$1"
  local pids=""
  pids="$(pgrep -f "$match" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 0
  fi
  # 先固定 PID，再发送 SIGTERM，避免 pkill -f 在脚本自身或其他命令行
  # 中出现相似路径时误匹配。
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 25); do
    pids="$(pgrep -f "$match" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
      return 0
    fi
    sleep 0.2
  done
  echo "⚠️  App 未响应 SIGTERM，改用 SIGKILL：$match（PID: $pids）" >&2
  kill -KILL $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    pids="$(pgrep -f "$match" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
      return 0
    fi
    sleep 0.2
  done
  echo "❌ App 进程仍未退出：$match（PID: $pids）" >&2
  return 1
}

echo "==> 构建 standalone 分发包 (build_app.sh -> package_app.sh)"
bash "$PROJECT_DIR/build_app.sh"

if [ ! -d "$PACKAGE_APP" ]; then
  echo "❌ standalone App 未生成：$PACKAGE_APP" >&2
  exit 1
fi
if [ ! -x "$PACKAGE_APP/Contents/Resources/whisper-runtime/bin/whisper-cli" ]; then
  echo "❌ standalone App 缺少内置 whisper-cli：$PACKAGE_APP" >&2
  exit 1
fi

# 先在 /Applications 中准备新包；复制失败时保留旧安装，避免半成品覆盖当前 App。
rm -rf "$STAGING"
ditto "$PACKAGE_APP" "$STAGING"

echo "==> 退出运行中的 App"
if ! stop_app "${INSTALLED}/Contents/MacOS/${APP_NAME}"; then
  exit 1
fi

echo "==> 替换 ${INSTALLED}"
if [ -e "$INSTALLED" ]; then
  rm -rf "$INSTALLED"
fi
mv "$STAGING" "$INSTALLED"

echo "==> 启动"
open "$INSTALLED"
echo "✅ 已构建 standalone 分发包并部署到 ${INSTALLED}"
echo "   分发 Zip：$PROJECT_DIR/release/${APP_NAME}-macOS-arm64.zip"
