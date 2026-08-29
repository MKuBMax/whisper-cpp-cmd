#!/bin/bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WhisperCppCmd"
APP_PATH="$PROJECT_DIR/${APP_NAME}.app"
ARM64_PYTHON="${WHISPER_CPP_CMD_PYTHON:-$PROJECT_DIR/.venv-arm64/bin/python}"

ASSET_DIR="$PROJECT_DIR/.py2app-assets"
BUILD_DIR="$PROJECT_DIR/.py2app-build"
DIST_DIR="$PROJECT_DIR/.py2app-dist"
ICON_SOURCE="$PROJECT_DIR/icons/app_icon.svg"
ICON_PATH="$ASSET_DIR/${APP_NAME}.icns"
ICON_WORK_DIR="$(mktemp -d /tmp/${APP_NAME}.icon.XXXXXX)"
ICONSET_DIR="$ICON_WORK_DIR/${APP_NAME}.iconset"
ICON_RENDER_DIR="$(mktemp -d /tmp/${APP_NAME}.icon-render.XXXXXX)"

cleanup() {
    rm -rf "$ICON_WORK_DIR" "$ICON_RENDER_DIR"
}

trap cleanup EXIT

mkdir -p "$ASSET_DIR" "$ICONSET_DIR"
rm -rf "$BUILD_DIR" "$DIST_DIR" "$APP_PATH"

if [ -f "$ICON_SOURCE" ]; then
    qlmanage -t -s 1024 -o "$ICON_RENDER_DIR" "$ICON_SOURCE" >/dev/null 2>&1 || true
    ICON_RENDERED_PATH="$ICON_RENDER_DIR/$(basename "$ICON_SOURCE").png"

    if [ -f "$ICON_RENDERED_PATH" ]; then
        for size in 16 32 128 256 512; do
            sips -z "$size" "$size" "$ICON_RENDERED_PATH" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
            sips -z "$((size * 2))" "$((size * 2))" "$ICON_RENDERED_PATH" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
        done

        iconutil -c icns "$ICONSET_DIR" -o "$ICON_PATH"
    fi
fi

cd "$PROJECT_DIR"

if [ ! -x "$ARM64_PYTHON" ]; then
    echo "❌ 找不到项目 Python：$ARM64_PYTHON" >&2
    echo "   请先按项目开发环境安装 .venv-arm64 依赖。" >&2
    exit 1
fi

"$ARM64_PYTHON" setup.py py2app -A \
    --dist-dir "$DIST_DIR" \
    --bdist-base "$BUILD_DIR"

ditto "$DIST_DIR/$APP_NAME.app" "$APP_PATH"

echo "已生成: $APP_PATH"
echo "构建 Python: ${ARM64_PYTHON}"
echo "模式: py2app alias（仅适用于当前电脑和当前项目目录）"
echo "启动方式: 双击 ${APP_NAME}.app"
echo "调试日志:"
echo "  - $PROJECT_DIR/logs/app-launcher.log"
echo "  - $PROJECT_DIR/logs/whisper-cpp-cmd.log"
