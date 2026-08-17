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
LIBOMP_PREFIX="$(brew --prefix libomp 2>/dev/null || true)"
GGML_VERSION="$(brew list --versions ggml 2>/dev/null | awk 'NF { print $NF }' | tail -1)"
if [ -z "$WHISPER_PREFIX" ] || [ -z "$GGML_PREFIX" ] || [ -z "$LIBOMP_PREFIX" ] || [ -z "$GGML_VERSION" ]; then
    echo "❌ 找不到 Homebrew 依赖，请先安装 whisper-cpp。" >&2
    echo "   brew install whisper-cpp libomp" >&2
    exit 1
fi

for build_tool in cmake curl; do
    if ! command -v "$build_tool" >/dev/null 2>&1; then
        echo "❌ 构建 standalone 分发包需要 $build_tool。" >&2
        exit 1
    fi
done

for required in \
    "$WHISPER_PREFIX/bin/whisper-cli" \
    "$WHISPER_PREFIX/bin/whisper-server" \
    "$WHISPER_PREFIX/lib/libwhisper.1.dylib" \
    "$GGML_PREFIX/lib/libggml.0.dylib" \
    "$GGML_PREFIX/lib/libggml-base.0.dylib" \
    "$LIBOMP_PREFIX/lib/libomp.dylib"; do
    if [ ! -e "$required" ]; then
        echo "❌ Homebrew 运行时文件不存在：$required" >&2
        exit 1
    fi
done

# Homebrew 的 ggml 动态库把本机 Cellar 的 libexec 目录编译进了
# GGML_BACKEND_DIR。直接复制它会在打包机上误加载 Homebrew 后端，换到同事电脑
# 又找不到后端。这里按同一 ggml 版本重新构建 arm64 runtime，不设置固定的
# GGML_BACKEND_DIR，并把 backend 插件安装到 whisper 可执行文件旁边。
GGML_BUILD_ROOT="$WORK_DIR/ggml-build"
GGML_SOURCE_PARENT="$GGML_BUILD_ROOT/src"
GGML_SOURCE_DIR="$GGML_SOURCE_PARENT/ggml-$GGML_VERSION"
BUILT_GGML_PREFIX="$GGML_BUILD_ROOT/prefix"
mkdir -p "$GGML_SOURCE_PARENT"
echo "正在构建 standalone ggml runtime（版本 ${GGML_VERSION}）..."
curl --fail --location --silent --show-error --retry 3 \
    "https://github.com/ggml-org/ggml/archive/refs/tags/v$GGML_VERSION.tar.gz" \
    | tar -xz -C "$GGML_SOURCE_PARENT"

if [ ! -d "$GGML_SOURCE_DIR" ]; then
    echo "❌ ggml 源码目录不存在：$GGML_SOURCE_DIR" >&2
    exit 1
fi

if ! cmake -S "$GGML_SOURCE_DIR" -B "$GGML_BUILD_ROOT/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_INSTALL_PREFIX="$BUILT_GGML_PREFIX" \
    -DBUILD_SHARED_LIBS=ON \
    -DGGML_ALL_WARNINGS=OFF \
    -DGGML_BACKEND_DIR= \
    -DGGML_BACKEND_DL=ON \
    -DGGML_BLAS=ON \
    -DGGML_BUILD_EXAMPLES=OFF \
    -DGGML_BUILD_TESTS=OFF \
    -DGGML_CCACHE=OFF \
    -DGGML_LTO=ON \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU_ALL_VARIANTS=ON \
    >"$GGML_BUILD_ROOT/configure.log" 2>&1; then
    tail -80 "$GGML_BUILD_ROOT/configure.log" >&2
    exit 1
fi

if ! cmake --build "$GGML_BUILD_ROOT/build" --parallel "$(sysctl -n hw.ncpu)" \
    >"$GGML_BUILD_ROOT/build.log" 2>&1; then
    tail -80 "$GGML_BUILD_ROOT/build.log" >&2
    exit 1
fi

if ! cmake --install "$GGML_BUILD_ROOT/build" \
    >"$GGML_BUILD_ROOT/install.log" 2>&1; then
    tail -80 "$GGML_BUILD_ROOT/install.log" >&2
    exit 1
fi

CUSTOM_GGML_LIB="$(find "$BUILT_GGML_PREFIX/lib" -maxdepth 1 -type f -name 'libggml.0.*.dylib' -print -quit)"
CUSTOM_GGML_BASE="$(find "$BUILT_GGML_PREFIX/lib" -maxdepth 1 -type f -name 'libggml-base.0.*.dylib' -print -quit)"
if [ -z "$CUSTOM_GGML_LIB" ] || [ -z "$CUSTOM_GGML_BASE" ]; then
    echo "❌ 自包含 ggml runtime 构建结果不完整。" >&2
    exit 1
fi

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
cp -L "$CUSTOM_GGML_LIB" "$RUNTIME_SOURCE/ggml/lib/libggml.0.dylib"
cp -L "$CUSTOM_GGML_BASE" "$RUNTIME_SOURCE/ggml/lib/libggml-base.0.dylib"
cp -L "$LIBOMP_PREFIX/lib/libomp.dylib" "$RUNTIME_SOURCE/lib/libomp.dylib"

# ggml 的默认加载路径包含可执行文件所在目录；把所有动态 backend 放在 bin
# 旁边，既不依赖当前工作目录，也不需要通过 GGML_BACKEND_PATH 猜测目录语义。
for backend in "$BUILT_GGML_PREFIX"/bin/libggml-*.so; do
    if [ -f "$backend" ]; then
        cp -L "$backend" "$RUNTIME_SOURCE/bin/"
    fi
done

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
install_name_tool -id "@rpath/libomp.dylib" \
    "$RUNTIME_SOURCE/lib/libomp.dylib"
for backend in "$RUNTIME_SOURCE"/bin/libggml-*.so; do
    install_name_tool -change "@rpath/libggml-base.0.dylib" \
        "@loader_path/../ggml/lib/libggml-base.0.dylib" \
        "$backend"
    if otool -L "$backend" | grep -Fq "$LIBOMP_PREFIX/lib/libomp.dylib"; then
        install_name_tool -change "$LIBOMP_PREFIX/lib/libomp.dylib" \
            "@loader_path/../lib/libomp.dylib" \
            "$backend"
    fi
done
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

BUNDLED_RUNTIME_DIR="$PACKAGE_DIR/$APP_NAME.app/Contents/Resources/whisper-runtime"
BUNDLED_CLI="$BUNDLED_RUNTIME_DIR/bin/whisper-cli"
BUNDLED_SERVER="$BUNDLED_RUNTIME_DIR/bin/whisper-server"
BUNDLED_BACKEND_DIR="$BUNDLED_RUNTIME_DIR/bin"

if ! compgen -G "$BUNDLED_BACKEND_DIR/libggml-*.so" >/dev/null; then
    echo "❌ standalone App 没有包含 ggml backend 插件。" >&2
    exit 1
fi

# 运行时 Mach-O 文件位于 Resources 下的自定义目录，codesign --deep 不会
# 可靠地替它们逐个更新签名；先逐个 ad hoc 重签，再签 App 外层。
for runtime_binary in \
    "$BUNDLED_CLI" \
    "$BUNDLED_SERVER" \
    "$BUNDLED_RUNTIME_DIR/lib/libwhisper.1.dylib" \
    "$BUNDLED_RUNTIME_DIR/lib/libomp.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml.0.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml-base.0.dylib"; do
    codesign --force --sign - "$runtime_binary"
done
for backend in "$BUNDLED_BACKEND_DIR"/libggml-*.so; do
    codesign --force --sign - "$backend"
done

# py2app 在复制 standalone 运行时之前已经签过 App；运行时文件又经过
# install_name_tool 修改并被放入 App 后，必须在所有内容就位后重新签名，
# 否则 CodeResources 会把这些文件识别为未封装资源。
codesign --force --deep --sign - "$PACKAGE_DIR/$APP_NAME.app"
codesign --verify --deep --strict "$PACKAGE_DIR/$APP_NAME.app"

for runtime_binary in \
    "$BUNDLED_CLI" \
    "$BUNDLED_SERVER" \
    "$BUNDLED_RUNTIME_DIR/lib/libwhisper.1.dylib" \
    "$BUNDLED_RUNTIME_DIR/lib/libomp.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml.0.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml-base.0.dylib"; do
    codesign --verify --verbose=2 "$runtime_binary" >/dev/null
done
for backend in "$BUNDLED_BACKEND_DIR"/libggml-*.so; do
    codesign --verify --verbose=2 "$backend" >/dev/null
done

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
for bundled_backend in "$BUNDLED_BACKEND_DIR"/libggml-*.so; do
    if ! file "$bundled_backend" | grep -q "arm64"; then
        echo "❌ 分发包里的 ggml backend 不是 arm64：$bundled_backend" >&2
        exit 1
    fi
    if otool -L "$bundled_backend" | grep -q "/opt/homebrew"; then
        echo "❌ ggml backend 仍引用 Homebrew 路径：$bundled_backend" >&2
        exit 1
    fi
done
for bundled_library in \
    "$BUNDLED_RUNTIME_DIR/lib/libwhisper.1.dylib" \
    "$BUNDLED_RUNTIME_DIR/lib/libomp.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml.0.dylib" \
    "$BUNDLED_RUNTIME_DIR/ggml/lib/libggml-base.0.dylib"; do
    if ! file "$bundled_library" | grep -q "arm64"; then
        echo "❌ 分发包里的动态库不是 arm64：$bundled_library" >&2
        exit 1
    fi
    if otool -L "$bundled_library" | grep -q "/opt/homebrew"; then
        echo "❌ 分发包动态库仍引用 Homebrew 路径：$bundled_library" >&2
        exit 1
    fi
done

# 在打包机上直接运行一次，确保动态库和 backend 的相对路径完整；这里主动
# 清除外部环境变量，测试 standalone 本身不依赖构建机的 ggml 配置。
env -u GGML_BACKEND_PATH "$BUNDLED_CLI" --help >/dev/null 2>&1
env -u GGML_BACKEND_PATH "$BUNDLED_SERVER" --help >/dev/null 2>&1

ditto -c -k --sequesterRsrc --keepParent "$PACKAGE_DIR" "$ZIP_PATH"

echo
echo "✅ 已生成 standalone 分发包："
echo "   App：$PACKAGE_DIR/$APP_NAME.app"
echo "   Zip：$ZIP_PATH"
echo "   目标：Apple Silicon macOS；模型不随包提供"
