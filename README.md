# WhisperCppCmd

WhisperCppCmd 是一个本地优先的 macOS 菜单栏语音输入工具。

按住右 Command 键说话，松开后自动转写，并把文字输入到录音开始时的光标位置。
没有可编辑光标时，文字会保留在剪贴板，并在屏幕上短暂提示。

## 功能

- 基于 whisper.cpp 在本机完成语音转写
- 默认使用中文识别，并支持切换模型、识别语言和中文简繁体输出
- 默认采用“松开后一次识别”的快速模式；旧的预览配置也会在松开时安全交付最终结果
- 可选 Silero VAD 静音裁剪
- 可编辑术语表，改善专有名词识别
- 支持自定义麦克风和录音热键
- 支持录音浮窗、录音时降低系统音量
- 支持自动粘贴和登录 macOS 后自动启动
- 首次启动使用一个原生窗口完成模型下载、权限检查和输入偏好设置
- 提供本地统计面板和 GitHub Releases 更新检查
- 支持 macOS 菜单栏运行

## 下载和使用

当前提供 Apple Silicon（arm64）测试版，可在 [Releases](https://github.com/MKuBMax/whisper-cpp-cmd/releases) 下载。

1. 将 `WhisperCppCmd.app` 拖到“应用程序”文件夹并启动。
2. 在欢迎窗口点击“下载推荐模型”，等待 `large-v3-turbo-q5_0` 下载完成。模型保存在：

   ```text
   ~/Library/Application Support/WhisperCppCmd/models/
   ```

   也可以把其他 whisper.cpp GGML 模型放入这个目录后在窗口或菜单栏切换。
3. 按窗口提示允许麦克风和辅助功能权限。辅助功能权限用于监听右 Command 和确认当前输入框；输入监控权限只在系统要求时补充开启。
4. 在任意可编辑输入框中按住右 Command 说话，松开后文字会输入到录音开始时的光标。桌面、Finder、密码框等无法确认光标的位置会自动复制到剪贴板。
5. 从菜单栏图标或 Dock 重新打开主窗口，可切换麦克风、识别语言、模型和其他偏好。默认语言为“中文”；需要中英混合内容时可手动选择“自动识别（多语言）”。

模型下载页面：[whisper.cpp Models](https://huggingface.co/ggerganov/whisper.cpp/tree/main)

启用 VAD 时，App 会在本地模型目录中使用 Silero VAD 模型；也可以在菜单栏中关闭 VAD。停止录音后还会做一次保守的空录音保护：只跳过数字静音/无效采样，模型返回的非空结果不会因为置信度被拦截。

“统计面板”读取本地 `logs/perf.jsonl`，展示听写次数、成功率、处理耗时、RTF 和最近 7 天使用量；不会上传统计数据。App 默认每天后台检查一次 GitHub Releases，只有发现新版本才提示；可以在设置窗口关闭自动检查。

## 权限

首次运行通常需要允许：

- 麦克风
- 辅助功能

如果右 Command 没有反应，请在“系统设置 → 隐私与安全性”中确认当前的 `WhisperCppCmd.app` 已获得辅助功能权限；系统若单独要求输入监控，再按窗口提示开启。菜单栏和主窗口中的权限状态可以帮助确认当前授权情况。

默认测试包使用 ad hoc 签名，首次打开可能需要在“系统设置 → 隐私与安全性”中点击“仍要打开”。正式发布时可用 Apple Developer ID 签名并公证，见下方“签名和公证”。

## 从源码构建

本机 standalone 打包并审核（会替换 `/Applications` 中的 App）：

```bash
bash ship_app.sh
```

本机流程不使用 alias App。`ship_app.sh` 会调用 `build_app.sh`/`package_app.sh` 生成 standalone 包，替换 `/Applications/WhisperCppCmd.app` 并启动。

只构建 standalone 分发包、不替换 `/Applications`：

```bash
bash build_app.sh
```

构建机需要 Apple Silicon、项目 `.venv-arm64`、Homebrew 的 `whisper-cpp`/`libomp`、CMake 和网络连接。生成的 zip 位于：

```text
release/WhisperCppCmd-macOS-arm64.zip
```

分发包已经包含 Python 依赖、whisper.cpp 运行时和 ggml backend，但不包含体积较大的 Whisper 模型；同事不需要安装 Homebrew。

版本号统一保存在仓库根目录的 `VERSION`（当前值以该文件为准）。构建时可以用 `WHISPER_CPP_CMD_VERSION=1.2.3` 临时覆盖；必须是三段 SemVer，预发布标签可选。

### 签名和公证

没有 Apple Developer ID 时，`package_app.sh` 会生成可校验完整性的 ad hoc 签名包。正式分发时，在已登录 Apple 账号且钥匙串中有 `Developer ID Application` 证书的构建机上设置：

```bash
export WHISPER_CPP_CMD_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export WHISPER_CPP_CMD_NOTARY_PROFILE="whispercppcmd-notary"
export WHISPER_CPP_CMD_NOTARIZE=true
bash package_app.sh
```

`WHISPER_CPP_CMD_NOTARY_PROFILE` 是用 `xcrun notarytool store-credentials` 保存到钥匙串的 profile 名称。脚本会在公证完成后 stapling，并重新验证 App；未设置 `WHISPER_CPP_CMD_NOTARIZE=true` 时只签名不提交公证。

更新器只对 standalone App 生效，不会替换开发用 alias 包。下载时检查 GitHub HTTPS 重定向、zip 路径/符号链接、App 结构和 `codesign --verify --deep --strict`；已配置 Developer ID 的安装还要求新包与当前 App 使用同一个 Team ID。当前 App 退出后由 App 内独立 helper 完成替换，保留 `.previous`；新版本无法启动时会保留 `.failed.*` 并自动恢复旧版本。

## 隐私

录音和转写全部在本机完成，不上传音频或转写文本，也没有遥测和云端 LLM。模型、配置、历史记录和日志保存在本机：

```text
~/Library/Application Support/WhisperCppCmd/
```

## License

本项目使用 [MIT License](LICENSE)。第三方组件和模型说明见 [THIRD_PARTY_NOTICES.txt](distribution/THIRD_PARTY_NOTICES.txt)。
