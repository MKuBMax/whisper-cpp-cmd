#!/bin/bash
# 构建可交给同事的 Apple Silicon standalone 分发包。
# 用法：bash package_app.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WhisperCppCmd"
ARM64_PYTHON="$PROJECT_DIR/.venv-arm64/bin/python3"
RELEASE_DIR="$PROJECT_DIR/release"
PACKAGE_NAME="$APP_NAME-macOS-arm64"
PACKAGE_DIR="$RELEASE_DIR/$PACKAGE_NAME"
ZIP_PATH="$RELEASE_DIR/$PACKAGE_NAME.zip"
WORK_DIR="$(mktemp -d "/tmp/$APP_NAME.distribution.XXXXXX")"
RUNTIME_SOURCE="$WORK_DIR/whisper-runtime"
PY2APP_BUILD_DIR="$WORK_DIR/py2app-build"
PY2APP_DIST_DIR="$WORK_DIR/py2app-dist"
ICON_SOURCE="$PROJECT_DIR/icons/app_icon.svg"
ICON_ASSET_DIR="$PROJECT_DIR/.py2app-assets"
ICON_PATH="$ICON_ASSET_DIR/${APP_NAME}.icns"
ICONSET_DIR="$WORK_DIR/${APP_NAME}.iconset"
ICON_RENDER_DIR="$WORK_DIR/${APP_NAME}.icon-render"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

if [ "$(uname -m)" != "arm64" ]; then
    echo "❌ 当前只构建 Apple Silicon 包（需要在 arm64 Mac 上运行）" >&2
    exit 1
fi

if [ ! -x "$ARM64_PYTHON" ]; then
    echo "❌ 找不到项目 Python：$ARM64_PYTHON" >&2
    echo "   请先按项目开发环境安装依赖。" >&2
    exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
    echo "❌ 构建分发包需要 Homebrew，用它读取本机 whisper-cpp 运行时。" >&2
    exit 1
fi

WHISPER_PREFIX="$(brew --prefix whisper-cpp 2>/dev/null || true)"
GGML_PREFIX="$(brew --prefix ggml 2>/dev/null || true)"
if [ -z "$WHISPER_PREFIX" ] || [ -z "$GGML_PREFIX" ]; then
    echo "❌ 找不到 Homebrew 依赖，请先安装 whisper-cpp。" >&2
    echo "   brew install whisper-cpp" >&2
    exit 1
fi

for required in \
    "$WHISPER_PREFIX/bin/whisper-cli" \
    "$WHISPER_PREFIX/bin/whisper-server" \
    "$WHISPER_PREFIX/lib/libwhisper.1.dylib" \
    "$GGML_PREFIX/lib/libggml.0.dylib" \
    "$GGML_PREFIX/lib/libggml-base.0.dylib"; do
    if [ ! -e "$required" ]; then
        echo "❌ Homebrew 运行时文件不存在：$required" >&2
        exit 1
    fi
done

mkdir -p \
    "$RUNTIME_SOURCE/bin" \
    "$RUNTIME_SOURCE/lib" \
    "$RUNTIME_SOURCE/ggml/lib"

# standalone 构建也要从当前 SVG 重新生成 icns，避免沿用上一次 alias 构建的旧图标。
mkdir -p "$ICON_ASSET_DIR" "$ICONSET_DIR" "$ICON_RENDER_DIR"
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

# 跟随 Homebrew symlink 复制真实文件，避免分发包依赖打包机的 Cellar 路径。
cp -L "$WHISPER_PREFIX/bin/whisper-cli" "$RUNTIME_SOURCE/bin/whisper-cli"
cp -L "$WHISPER_PREFIX/bin/whisper-server" "$RUNTIME_SOURCE/bin/whisper-server"
cp -L "$WHISPER_PREFIX/lib/libwhisper.1.dylib" "$RUNTIME_SOURCE/lib/libwhisper.1.dylib"
cp -L "$GGML_PREFIX/lib/libggml.0.dylib" "$RUNTIME_SOURCE/ggml/lib/libggml.0.dylib"
cp -L "$GGML_PREFIX/lib/libggml-base.0.dylib" "$RUNTIME_SOURCE/ggml/lib/libggml-base.0.dylib"

# whisper.cpp 的 Homebrew bottle 默认指向 /opt/homebrew/opt/ggml；改成包内相对
# 路径后，同一个 App 可在没有 Homebrew 的同事电脑上运行。
for executable in \
    "$RUNTIME_SOURCE/bin/whisper-cli" \
    "$RUNTIME_SOURCE/bin/whisper-server"; do
    install_name_tool -change "$GGML_PREFIX/lib/libggml.0.dylib" \
        "@loader_path/../ggml/lib/libggml.0.dylib" \
        "$executable"
    install_name_tool -change "$GGML_PREFIX/lib/libggml-base.0.dylib" \
        "@loader_path/../ggml/lib/libggml-base.0.dylib" \
        "$executable"
done
install_name_tool -id "@rpath/libwhisper.1.dylib" \
    "$RUNTIME_SOURCE/lib/libwhisper.1.dylib"
install_name_tool -change "$GGML_PREFIX/lib/libggml.0.dylib" \
    "@loader_path/../ggml/lib/libggml.0.dylib" \
    "$RUNTIME_SOURCE/lib/libwhisper.1.dylib"
install_name_tool -change "$GGML_PREFIX/lib/libggml-base.0.dylib" \
    "@loader_path/../ggml/lib/libggml-base.0.dylib" \
    "$RUNTIME_SOURCE/lib/libwhisper.1.dylib"
install_name_tool -id "@rpath/libggml.0.dylib" \
    "$RUNTIME_SOURCE/ggml/lib/libggml.0.dylib"
install_name_tool -change "@rpath/libggml-base.0.dylib" \
    "@loader_path/libggml-base.0.dylib" \
    "$RUNTIME_SOURCE/ggml/lib/libggml.0.dylib"
install_name_tool -id "@rpath/libggml-base.0.dylib" \
    "$RUNTIME_SOURCE/ggml/lib/libggml-base.0.dylib"
chmod +x "$RUNTIME_SOURCE/bin/whisper-cli" "$RUNTIME_SOURCE/bin/whisper-server"

rm -rf "$RELEASE_DIR"
mkdir -p "$PACKAGE_DIR"

cd "$PROJECT_DIR"
    "$ARM64_PYTHON" setup.py py2app \
    --dist-dir "$PY2APP_DIST_DIR" \
    --bdist-base "$PY2APP_BUILD_DIR"

ditto "$PY2APP_DIST_DIR/$APP_NAME.app" "$PACKAGE_DIR/$APP_NAME.app"
FINAL_RUNTIME_DIR="$PACKAGE_DIR/$APP_NAME.app/Contents/Resources/whisper-runtime"
# 在 py2app 完成 Mach-O 处理后再放入运行时，避免它改写运行时自己的依赖路径。
ditto "$RUNTIME_SOURCE" "$FINAL_RUNTIME_DIR"
cp "$PROJECT_DIR/distribution/README.md" "$PACKAGE_DIR/README.md"
cp "$PROJECT_DIR/distribution/Prepare WhisperCppCmd.command" "$PACKAGE_DIR/Prepare WhisperCppCmd.command"
cp "$PROJECT_DIR/distribution/THIRD_PARTY_NOTICES.txt" "$PACKAGE_DIR/THIRD_PARTY_NOTICES.txt"
chmod +x "$PACKAGE_DIR/Prepare WhisperCppCmd.command"

BUNDLED_CLI="$PACKAGE_DIR/$APP_NAME.app/Contents/Resources/whisper-runtime/bin/whisper-cli"
BUNDLED_SERVER="$PACKAGE_DIR/$APP_NAME.app/Contents/Resources/whisper-runtime/bin/whisper-server"
if [ ! -x "$BUNDLED_CLI" ] || [ ! -x "$BUNDLED_SERVER" ]; then
    echo "❌ standalone App 没有包含 whisper.cpp 运行时。" >&2
    exit 1
fi
for bundled_binary in "$BUNDLED_CLI" "$BUNDLED_SERVER"; do
    if ! file "$bundled_binary" | grep -q "arm64"; then
        echo "❌ 分发包里的二进制不是 arm64：$bundled_binary" >&2
        exit 1
    fi
    if otool -L "$bundled_binary" | grep -q "/opt/homebrew"; then
        echo "❌ 分发包仍引用 Homebrew 路径：$bundled_binary" >&2
        exit 1
    fi
done

ditto -c -k --sequesterRsrc --keepParent "$PACKAGE_DIR" "$ZIP_PATH"

echo
echo "✅ 已生成 standalone 分发包："
echo "   App：$PACKAGE_DIR/$APP_NAME.app"
echo "   Zip：$ZIP_PATH"
echo "   目标：Apple Silicon macOS；模型不随包提供"
