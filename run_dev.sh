#!/bin/bash
# 已废弃：裸 Python 启动方式已下线。
#
# 裸 Python 无 bundle 权限身份，麦克风授权不可靠，且与 DEV App 双跑会抢热键。
# 开发模式统一入口：bash run_dev_app.sh（DEV App，alias 引用源码，改完即生效）。
# 测试不受影响，仍用 .venv-arm64/bin/python -m pytest tests/。
set -euo pipefail
echo "❌ run_dev.sh 已废弃：裸 Python 启动方式已下线。" >&2
echo "   请改用：bash run_dev_app.sh" >&2
exit 1
