#!/bin/bash
# 构建本机使用和分发的 Apple Silicon standalone 包。
# 需要替换 /Applications 中的安装时，请运行 bash ship_app.sh。
# 用法：bash build_app.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 保留 build_app.sh 作为本机既有 SOP 的兼容入口，但统一转到 standalone
# 构建器；这里不再生成项目根目录的 py2app alias App。
exec bash "$PROJECT_DIR/package_app.sh"
