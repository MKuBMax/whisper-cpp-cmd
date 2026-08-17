# 扬声器音乐串扰与 ducking

用户实测确认：内置扬声器播放音乐时，音乐会串入内置麦克风，明显降低转写成功率。

## 调研结论

- FFT 谱降噪对音乐基本无效；它主要处理稳态噪声底，音乐调性信号会被保留。
- whisper-server 没有可直接解决该问题的降噪开关。
- 普遍的听写工具靠 raw 采集和戴耳机规避；真正的系统级解决方案是 macOS VPIO/AEC。
- sounddevice/PortAudio 不能直接打开 macOS Voice Processing。

## 已落地的 Tier 0

录音期间压低系统输出音量，从声源减少音乐能量，不碰采集管线。

core/media_ducker.py 负责：

- 录音开始时保存并降低输出音量。
- 录音结束时恢复原音量。
- 读取回音量验证设置是否真的生效。
- 耳机、USB 和虚拟输出设备自动跳过。
- 配置 duck_media 和 duck_volume。

当前设备判定优先使用 CoreAudio transport type；system_profiler 失败时退回设备名判断。3.5mm 有线耳机可能仍被识别为 built-in，这是已知边界。

begin 和 restore 已异步化，并把多次 osascript 合并为单次脚本，避免录音中 UI 反馈被约 667 ms 的同步调用阻塞。

## Tier 1 选项

如果 Tier 0 不够，唯一有希望真正抹除音乐的是 VPIO/AEC。但应使用签名 Swift 子进程持有 AudioUnit 并通过 stdout 输出 int16 PCM，不能在主 Python 进程里直接持有；这与当前采集 worker 隔离架构一致。

