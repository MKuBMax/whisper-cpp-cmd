# WhisperCppCmd

WhisperCppCmd 是一个本地优先的 macOS 菜单栏语音输入工具。

按住右 Command 键说话，松开后自动转写，并把文字输入到当前光标位置。

## 功能

- 基于 whisper.cpp 在本机完成语音转写
- 支持切换模型、识别语言和中文简繁体输出
- 支持预览模式和快速模式
- 可选 Silero VAD 静音裁剪
- 可编辑术语表，改善专有名词识别
- 支持自定义麦克风和录音热键
- 支持录音浮窗、录音时降低系统音量
- 支持自动粘贴和登录 macOS 后自动启动
- 支持 macOS 菜单栏运行

## 下载和使用

当前提供 Apple Silicon（arm64）测试版，可在 [Releases](https://github.com/MKuBMax/whisper-cpp-cmd/releases) 下载。

1. 将 `WhisperCppCmd.app` 拖到“应用程序”文件夹。
2. 运行 `Prepare WhisperCppCmd.command`，打开模型目录。
3. 下载一个 whisper.cpp GGML 模型，放入：

   ```text
   ~/Library/Application Support/WhisperCppCmd/models/
   ```

   默认模型是 `ggml-large-v3.bin`，也可以放入其他模型并在菜单栏切换。
4. 双击启动 `WhisperCppCmd.app`。
5. 在菜单栏中选择麦克风、识别语言、模型和其他选项。

模型下载页面：[whisper.cpp Models](https://huggingface.co/ggerganov/whisper.cpp/tree/main)

启用 VAD 时，App 会在本地模型目录中自动下载 Silero VAD 模型；也可以在菜单栏中关闭 VAD。

## 权限

首次运行通常需要允许：

- 麦克风
- 辅助功能
- 输入监控

如果右 Command 没有反应，请在“系统设置 → 隐私与安全性”中确认当前的 `WhisperCppCmd.app` 已获得辅助功能和输入监控权限，然后重新打开 App。菜单栏中的权限状态可以帮助确认当前授权情况。

由于测试版尚未使用 Apple Developer ID 签名和 notarization，首次打开可能需要在“系统设置 → 隐私与安全性”中点击“仍要打开”。

## 从源码构建

本地开发和审核：

```bash
bash build_app.sh
```

这会生成仅适用于当前开发环境的 alias App，不适合直接分发。

构建 standalone 分发包：

```bash
bash package_app.sh
```

构建机需要 Apple Silicon、项目 `.venv-arm64`、Homebrew 的 `whisper-cpp`/`libomp`、CMake 和网络连接。生成的 zip 位于：

```text
release/WhisperCppCmd-macOS-arm64.zip
```

分发包已经包含 Python 依赖、whisper.cpp 运行时和 ggml backend，但不包含体积较大的 Whisper 模型；同事不需要安装 Homebrew。

## 隐私

录音、转写和文本处理全部在本机完成，不上传音频或转写文本，也没有遥测和云端 LLM。模型、配置、历史记录和日志保存在本机：

```text
~/Library/Application Support/WhisperCppCmd/
```

## License

本项目使用 [MIT License](LICENSE)。第三方组件和模型说明见 [THIRD_PARTY_NOTICES.txt](distribution/THIRD_PARTY_NOTICES.txt)。
