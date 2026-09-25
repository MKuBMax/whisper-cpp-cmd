# 最近识别音频归档

为分析长语音识别失败和幻觉，App 将每次交给本地识别引擎的音频保存在 `audio-recordings/`，并为每段音频写一份 JSON 元数据。WAV 在系统参考消除和 Processor 预处理之后生成；`whisper-server` 直接读取归档的 WAV，因此文件内容就是识别请求使用的音频。

## 当前行为

- 仅保存通过空录音保护、即将交给识别管线的录音；短录音、无效采样和数字静音不会归档。
- 每条 JSON 保存模型、语言、VAD、参考消除状态、预处理配置、时长、trace id、识别文本和成功/失败状态；不保存转写 prompt 或术语表。
- 最多保留 10 个 WAV/JSON 记录对；写入新记录后删除更早记录。
- App Support 中的归档目录和文件设为当前用户可读写。菜单栏“最近语音记录”子菜单列出最新条目；选择条目可试听 WAV 并查看识别文字。子菜单底部可以在 Finder 中打开目录并手动删除记录。
- 归档失败只记录 warning，不阻止识别。

默认 DEV App 和正式 App 的目录为 `~/Library/Application Support/WhisperCppCmd/audio-recordings/`；使用 `WHISPER_CPP_CMD_DATA_DIR` 时跟随该数据根目录。
