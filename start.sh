#!/bin/bash
# 兼容入口：开发模式请使用 run_dev.sh
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$PROJECT_DIR/run_dev.sh" "$@"
