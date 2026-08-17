# 项目概览与架构

WhisperCppCmd 是一个 macOS 本地语音输入菜单栏 App，使用 Python + py2app，目标平台是 Apple Silicon。

核心流程：

1. 按住右 Command。
2. 使用 sounddevice 录音。
3. 调用外部 whisper-cli 或 whisper-server 二进制转写。
4. 在当前光标处插入文本，按 iTerm2 原生、CGEvent、System Events、AX 等路径逐级回退。
5. 使用 OpenCC 做简繁归一。
6. 写入 history.json。

模型位于 models/，当前包含 large-v3、large-v3-turbo 和 q5_0 等变体。

## 核心代码区域

- app/controller.py：应用协调器和状态机，当前仍是较大的单体文件。
- ui/status_bar.py：菜单栏 NSMenu。
- ui/overlay_window.py：录音浮窗。
- core/pipeline.py、core/model.py：转写管线和模型进程。
- core/audio_source.py、core/audio_worker.py：采集客户端和隔离的音频 worker。
- core/clipboard.py：剪贴板与文本插入。
- core/live_dictation.py：实时预览。
- core/output.py：输出持久化。
- config/settings.py：活配置。

## 硬约束

- 本地优先：音频和文本不上传。
- 单用户、单人维护。
- 优先最小、可回滚的改动。
- 不为了不可能场景增加复杂错误处理。
- 不引入强制云端 LLM、常驻麦克风或额外重依赖来解决局部问题。

