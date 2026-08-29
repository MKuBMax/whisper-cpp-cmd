#!/bin/bash
# 在主 App 退出后，以独立进程安全替换已下载并校验过的 bundle。
#
# 参数：主 App PID、当前 bundle 路径、staged bundle 路径。这个脚本故意不
# 依赖 Python 或项目源码，因为它需要在旧 App 退出后继续存活。
set -euo pipefail

if [ "$#" -ne 3 ]; then
    exit 2
fi

APP_PID="$1"
CURRENT_APP="$2"
STAGED_APP="$3"

case "$APP_PID" in
    ''|*[!0-9]*) exit 2 ;;
esac
case "$CURRENT_APP" in
    /*/WhisperCppCmd.app) ;;
    *) exit 3 ;;
esac
case "$STAGED_APP" in
    /*/WhisperCppCmd-*-staged.app) ;;
    *) exit 4 ;;
esac

if [ ! -d "$CURRENT_APP" ] || [ -L "$CURRENT_APP" ] || \
   [ ! -d "$STAGED_APP" ] || [ -L "$STAGED_APP" ]; then
    exit 5
fi

CURRENT_EXECUTABLE="$CURRENT_APP/Contents/MacOS/WhisperCppCmd"
STAGED_EXECUTABLE="$STAGED_APP/Contents/MacOS/WhisperCppCmd"
if [ ! -x "$CURRENT_EXECUTABLE" ] || [ ! -x "$STAGED_EXECUTABLE" ]; then
    exit 5
fi

process_matches_current_app() {
    kill -0 "$APP_PID" 2>/dev/null || return 1
    # 仅凭 kill -0 有 PID 重用风险；macOS 的 ps 可读到完整 command line，
    # 因而在替换前确认这个 PID 确实仍是当前 WhisperCppCmd bundle。
    local command_line
    command_line="$(ps -p "$APP_PID" -o command= 2>/dev/null || true)"
    case "$command_line" in
        *"$CURRENT_EXECUTABLE"*) return 0 ;;
        *) return 1 ;;
    esac
}

relaunch_current_after_failure() {
    # helper 可能在主 App 调用 shutdown() 前就发现签名/路径问题；等待父进程
    # 退出后再打开旧 bundle，避免同时启动两个实例或让用户停在无菜单栏状态。
    for _ in $(seq 1 120); do
        if ! process_matches_current_app; then
            break
        fi
        sleep 1
    done
    if ! process_matches_current_app; then
        open -n "$CURRENT_APP" >/dev/null 2>&1 || true
    fi
}

validate_bundle_identity() {
    local app_path="$1"
    local bundle_id
    local bundle_executable
    [ -f "$app_path/Contents/Info.plist" ] || return 1
    bundle_id="$(plutil -extract CFBundleIdentifier raw -o - \
        "$app_path/Contents/Info.plist" 2>/dev/null)" || return 1
    bundle_executable="$(plutil -extract CFBundleExecutable raw -o - \
        "$app_path/Contents/Info.plist" 2>/dev/null)" || return 1
    [ "$bundle_id" = "com.mkbm.whispercppcmd" ] || return 1
    [ "$bundle_executable" = "WhisperCppCmd" ] || return 1
}

# Python 校验后 helper 再确认一次 bundle 身份，防止 staged 路径在两个
# 进程交接期间被替换成另一个同签名 App。
if ! validate_bundle_identity "$CURRENT_APP" || \
   ! validate_bundle_identity "$STAGED_APP"; then
    relaunch_current_after_failure
    exit 5
fi

# helper 自身再次校验，防止 staged bundle 在 Python 下载线程结束后被替换。
if ! codesign --verify --deep --strict "$STAGED_APP" >/dev/null 2>&1; then
    relaunch_current_after_failure
    exit 8
fi
if ! codesign --verify --deep --strict "$CURRENT_APP" >/dev/null 2>&1; then
    relaunch_current_after_failure
    exit 8
fi

signature_details() {
    codesign --display --verbose=4 "$1" 2>&1
}

if ! CURRENT_SIGNATURE="$(signature_details "$CURRENT_APP")"; then
    relaunch_current_after_failure
    exit 8
fi
if ! STAGED_SIGNATURE="$(signature_details "$STAGED_APP")"; then
    relaunch_current_after_failure
    exit 8
fi

current_team="$(printf '%s\n' "$CURRENT_SIGNATURE" | awk -F= '$1 == "TeamIdentifier" { print $2; exit }')"
staged_team="$(printf '%s\n' "$STAGED_SIGNATURE" | awk -F= '$1 == "TeamIdentifier" { print $2; exit }')"
case "$current_team" in
    "not set"|"not_set") current_team="" ;;
esac
case "$staged_team" in
    "not set"|"not_set") staged_team="" ;;
esac
current_adhoc=0
staged_adhoc=0
if printf '%s\n' "$CURRENT_SIGNATURE" | grep -Fq 'Signature=adhoc'; then current_adhoc=1; fi
if printf '%s\n' "$STAGED_SIGNATURE" | grep -Fq 'Signature=adhoc'; then staged_adhoc=1; fi
current_developer_id=0
staged_developer_id=0
if printf '%s\n' "$CURRENT_SIGNATURE" | grep -Fq 'Authority=Developer ID Application:'; then
    current_developer_id=1
fi
if printf '%s\n' "$STAGED_SIGNATURE" | grep -Fq 'Authority=Developer ID Application:'; then
    staged_developer_id=1
fi

# 固定“签名连续性”：Developer ID App 必须保持同一个 Team ID；内部 ad hoc
# 包只能替换为另一个 ad hoc 包。否则即使 codesign 封装校验通过，也不安装
# 一个来源完全不同的 bundle。
if [ -n "$current_team" ]; then
    if [ "$staged_team" != "$current_team" ] || [ "$staged_adhoc" -eq 1 ] || \
       [ "$staged_developer_id" -ne "$current_developer_id" ]; then
        relaunch_current_after_failure
        exit 8
    fi
elif [ "$current_adhoc" -eq 1 ]; then
    if [ "$staged_adhoc" -ne 1 ]; then
        relaunch_current_after_failure
        exit 8
    fi
else
    relaunch_current_after_failure
    exit 8
fi

# 最多等待两分钟，避免主进程异常退出时 helper 永久驻留；若 PID 已复用为
# 其它进程，process_matches_current_app 会把它视为已退出并继续，但不会误等。
for _ in $(seq 1 120); do
    if ! process_matches_current_app; then
        break
    fi
    sleep 1
done

if process_matches_current_app; then
    exit 6
fi

BACKUP_APP="${CURRENT_APP}.previous"
if [ -e "$BACKUP_APP" ] || [ -L "$BACKUP_APP" ]; then
    BACKUP_APP="${CURRENT_APP}.previous.$(date +%s)-$$"
    while [ -e "$BACKUP_APP" ] || [ -L "$BACKUP_APP" ]; do
        BACKUP_APP="${CURRENT_APP}.previous.$(date +%s)-$$-$RANDOM"
    done
fi

# 同一用户数据目录中可能残留上一次失败更新；只使用本次唯一的备份名，
# 不删除已有副本，方便人工回滚或诊断。
if ! mv "$CURRENT_APP" "$BACKUP_APP"; then
    # 安装目录可能由管理员拥有；更新失败时仍恢复用户的原 App，而不是
    # 让正常版本因为一次不可写替换而停在退出状态。
    open -n "$CURRENT_APP" >/dev/null 2>&1 || true
    exit 7
fi
if ! mv "$STAGED_APP" "$CURRENT_APP"; then
    if mv "$BACKUP_APP" "$CURRENT_APP"; then
        open -n "$CURRENT_APP" >/dev/null 2>&1 || true
    fi
    exit 7
fi

if ! codesign --verify --deep --strict "$CURRENT_APP" >/dev/null 2>&1; then
    FAILED_APP="${CURRENT_APP}.failed.$(date +%s)-$$"
    mv "$CURRENT_APP" "$FAILED_APP" || true
    if mv "$BACKUP_APP" "$CURRENT_APP"; then
        open -n "$CURRENT_APP" >/dev/null 2>&1 || true
    fi
    exit 8
fi

app_process_running() {
    local processes
    processes="$(ps -axo pid=,command= 2>/dev/null || true)"
    while IFS= read -r line; do
        case "$line" in
            *"$CURRENT_EXECUTABLE"*) return 0 ;;
        esac
    done <<< "$processes"
    return 1
}

# 用 open -n 强制启动新实例，然后至少确认新 bundle 的进程存活一段时间。
# 如果新包无法启动，保留失败包并恢复旧 bundle，避免更新把用户锁在坏版本。
if ! open -n "$CURRENT_APP" >/dev/null 2>&1; then
    launch_ok=0
else
    launch_ok=0
    for _ in $(seq 1 30); do
        if app_process_running; then
            launch_ok=1
            break
        fi
        sleep 1
    done
fi

if [ "$launch_ok" -eq 1 ]; then
    # 进程刚出现不等于已经成功启动；观察几秒覆盖 Python/模型初始化阶段
    # 的立即崩溃，再把成功结果交给用户。
    for _ in $(seq 1 5); do
        sleep 1
        if ! app_process_running; then
            launch_ok=0
            break
        fi
    done
fi

if [ "$launch_ok" -ne 1 ]; then
    FAILED_APP="${CURRENT_APP}.failed.$(date +%s)-$$"
    if [ -d "$CURRENT_APP" ] && [ ! -L "$CURRENT_APP" ]; then
        mv "$CURRENT_APP" "$FAILED_APP" || true
    fi
    if [ -d "$BACKUP_APP" ] && [ ! -L "$BACKUP_APP" ]; then
        mv "$BACKUP_APP" "$CURRENT_APP" || exit 9
        open -n "$CURRENT_APP" >/dev/null 2>&1 || true
    else
        exit 9
    fi
    exit 9
fi
