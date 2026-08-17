#!/bin/bash
# 安装 nowplaying-cli 工具

echo "📦 安装 nowplaying-cli..."

# 检查是否已安装
if command -v nowplaying-cli &> /dev/null; then
    echo "✅ nowplaying-cli 已安装"
    exit 0
fi

# 尝试通过 Homebrew 安装
if command -v brew &> /dev/null; then
    echo "🔍 通过 Homebrew 安装..."
    brew install nowplaying-cli
    
    if command -v nowplaying-cli &> /dev/null; then
        echo "✅ 安装成功！"
        echo ""
        echo "使用方法:"
        echo "  nowplaying-cli getPlaybackState  # 获取播放状态"
        echo "  nowplaying-cli togglePlayPause   # 播放/暂停"
        echo "  nowplaying-cli next              # 下一首"
        echo "  nowplaying-cli previous          # 上一首"
        exit 0
    else
        echo "❌ Homebrew 安装失败"
    fi
else
    echo "❌ 未检测到 Homebrew，请先安装 Homebrew:"
    echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi

echo ""
echo "⚠️  安装失败，语音输入时将不会自动暂停音乐"
echo "   你可以手动安装 nowplaying-cli，或继续使用（无音乐暂停功能）"
