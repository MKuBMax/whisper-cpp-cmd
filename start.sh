#!/bin/bash
# 兼容入口：开发模式请使用 run_dev_app.sh（DEV App，alias 引用源码）。
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$PROJECT_DIR/run_dev_app.sh" "$@"
