#!/bin/bash
# 单实例互斥：同一时间只允许一个 WhisperCmd 主进程。
# 覆盖正式版和 DEV 两个 App。裸 Python（run_dev.sh）已下线，不再匹配。
# 用法：bash scripts/single_instance.sh
#   停掉两个 App 的现存进程。停不掉返回非零，调用方必须阻止新实例启动。
set -euo pipefail

FORMAL_MATCH="/Applications/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd"
DEV_MATCH="WhisperCppCmdDev.app/Contents/MacOS/WhisperCppCmdDev"

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
  echo "❌ 进程仍未退出，阻止新实例启动：$match（PID: $pids）" >&2
  return 1
}

failed=0
for match in "$FORMAL_MATCH" "$DEV_MATCH"; do
  if ! stop_match "$match"; then
    failed=1
  fi
done

if [ "$failed" != "0" ]; then
  exit 1
fi
