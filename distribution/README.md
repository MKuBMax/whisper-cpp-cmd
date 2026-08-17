# WhisperCppCmd macOS（Apple Silicon）

这是一个未使用 Apple Developer ID 签名、未公证的 macOS standalone 测试分发包，目标是让同事先能直接试用。App 内部使用 ad hoc signature 校验完整性，但这不会让 macOS 将它识别为受信任的开发者软件。App 已经包含 Python 依赖和 whisper.cpp 运行时；模型不随包提供，需要自行下载。

## 安装和首次运行

1. 把 WhisperCppCmd.app 拖到“应用程序”文件夹（也可以直接在当前目录运行）。
2. 双击 Prepare WhisperCppCmd.command，它会创建并打开模型目录。
3. 下载一个 whisper.cpp 的 GGML 模型，把文件放到打开的目录：

   ~/Library/Application Support/WhisperCppCmd/models/

   默认模型名是 ggml-large-v3.bin。文件名需要与模型名一致，例如 ggml-large-v3-turbo-q5_0.bin 也可以，但需要在 config.json 中把 current_model 改成 large-v3-turbo-q5_0。
4. 双击 WhisperCppCmd.app。
5. 第一次使用时，App 会检查“麦克风”“辅助功能”和“输入监控”权限；若右 Command 无反应，分别确认系统设置中 WhisperCppCmd.app 的两项键盘权限已打开，或点击菜单栏中的“输入监控权限：未允许（打开设置）”直接进入对应页面。若状态异常，App 会弹出手动清理旧条目并重新添加当前 App 的操作指引。
6. 如果希望登录 macOS 后自动启动，在菜单栏打开「开机启动」；再次点击即可取消。

模型下载页：

https://huggingface.co/ggerganov/whisper.cpp/tree/main

## 运行时数据和日志

所有可变数据都在：

~/Library/Application Support/WhisperCppCmd/

其中包括 models/、config.json、history.json、glossary.txt 和 logs/。卸载 App 不会自动删除这些数据。

## macOS 安全提示

这个早期包没有 Apple Developer ID 签名和 notarization。首次打开可能会被 macOS 拦截；请先尝试打开 App，然后到“系统设置 → 隐私与安全性”，在安全性区域点击“仍要打开”，确认后再次打开 App。

当前包只支持 Apple Silicon（arm64）。Intel Mac 暂不包含在这一版分发目标内。
