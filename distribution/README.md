# WhisperCppCmd macOS（Apple Silicon）

这是一个 macOS standalone 分发包。没有配置 Developer ID 时，App 使用 ad hoc signature 校验完整性，但这不会让 macOS 将它识别为受信任的开发者软件；正式构建可以通过 `package_app.sh` 配置 Developer ID 签名和 Apple notarization。App 已经包含 Python 依赖和 whisper.cpp 运行时；模型不随包提供，需要自行下载。

## 安装和首次运行

1. 把 WhisperCppCmd.app 拖到“应用程序”文件夹（也可以直接在当前目录运行）。
2. 双击 Prepare WhisperCppCmd.command，它会创建并打开模型目录。
3. 下载一个 whisper.cpp 的 GGML 模型，把文件放到打开的目录：

   ~/Library/Application Support/WhisperCppCmd/models/

   默认模型名是 ggml-large-v3.bin。文件名需要与模型名一致，例如 ggml-large-v3-turbo-q5_0.bin 也可以，但需要在 config.json 中把 current_model 改成 large-v3-turbo-q5_0。
4. 双击 WhisperCppCmd.app。首次启动会显示向导；模型尚未放入时 App 仍会留在菜单栏，不会直接退出。
5. 放入模型后，在菜单栏选择「切换模型 → 重新加载当前模型」。App 也会检查“麦克风”“辅助功能”和“输入监控”权限；若右 Command 无反应，分别确认系统设置中 WhisperCppCmd.app 的两项键盘权限已打开，或点击菜单栏中的“输入监控权限：未允许（打开设置）”直接进入对应页面。若状态异常，App 会弹出手动清理旧条目并重新添加当前 App 的操作指引。
6. 可以在「打开设置…」管理更新检查等应用选项；「统计面板…」只读取本地性能日志。
7. 如果希望登录 macOS 后自动启动，在菜单栏打开「开机启动」；再次点击即可取消。

默认识别语言为「中文」。需要处理中英混合内容时，可在菜单栏手动选择「自动识别（多语言）」。VAD 之外还会跳过数字静音和无效采样；非空模型结果不会因置信度不足而被拦截。

模型下载页：

https://huggingface.co/ggerganov/whisper.cpp/tree/main

## 运行时数据和日志

所有可变数据都在：

~/Library/Application Support/WhisperCppCmd/

其中包括 models/、config.json、history.json、glossary.txt 和 logs/。卸载 App 不会自动删除这些数据。

## macOS 安全提示

默认构建的早期包没有 Apple Developer ID 签名和 notarization。首次打开可能会被 macOS 拦截；请先尝试打开 App，然后到“系统设置 → 隐私与安全性”，在安全性区域点击“仍要打开”，确认后再次打开 App。

当前包只支持 Apple Silicon（arm64）。Intel Mac 暂不包含在这一版分发目标内。

## 正式签名和公证

在构建机钥匙串中准备 `Developer ID Application` 证书，并先用 `xcrun notarytool store-credentials whispercppcmd-notary ...` 保存公证凭据，然后执行：

```bash
WHISPER_CPP_CMD_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
WHISPER_CPP_CMD_NOTARY_PROFILE="whispercppcmd-notary" \
WHISPER_CPP_CMD_NOTARIZE=true \
bash package_app.sh
```

脚本会逐个签名内置运行时、签名 App、提交 zip、公证完成后 stapling，并验证最终包。没有证书或 profile 时不要打开 `WHISPER_CPP_CMD_NOTARIZE`。

仓库根目录的 `VERSION` 是版本唯一来源；临时发布版本可通过 `WHISPER_CPP_CMD_VERSION=1.2.3` 覆盖（必须是三段 SemVer）。构建脚本会把覆盖值同步写入 App 内的 `Contents/Resources/VERSION`，确保菜单栏版本和 Info.plist 不漂移。

## 使用中的入口

- 「检查更新…」：检查 GitHub Releases。存在匹配的 arm64 zip 且签名校验通过时，可下载并在退出当前 App 后安装；开发用 alias 包不会自动替换。更新器会校验 HTTPS 下载、zip 路径、符号链接、App 结构和签名连续性（Developer ID 使用相同 Team ID，ad hoc 仅接受 ad hoc），旧版本保留为 `.previous` 备份；新包无法启动时保留 `.failed.*` 并恢复旧版本。

App 默认每天后台检查一次版本；自动检查只读取 GitHub Release 元数据，发现新版本才提示，不会自动下载、关闭或替换正在运行的 App。可在「打开设置…」中关闭。
