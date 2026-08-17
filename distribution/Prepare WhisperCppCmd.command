#!/bin/zsh

set -euo pipefail

APP_DATA_DIR="$HOME/Library/Application Support/WhisperCppCmd"
MODEL_DIR="$APP_DATA_DIR/models"

mkdir -p "$MODEL_DIR"
open "$MODEL_DIR"

echo
echo "模型目录已打开："
echo "$MODEL_DIR"
echo
echo "请把 ggml 模型文件放进这个目录，然后双击 WhisperCppCmd.app。"
echo "按回车关闭此窗口。"
read -r
