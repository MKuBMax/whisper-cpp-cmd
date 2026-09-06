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

已落地为系统音频参考消除（ref_cancel，默认关闭）：录音时经 ScreenCaptureKit 抓系统输出做参考，对齐后块级门控压制扬声器串音。

- 采集：Swift 子进程 core/sysref_capture 经 SCStream 只抓音频，输出 16kHz 单声道 PCM，构建见 core/SYSREF_BUILD.md。
- 消除：core/ref_cancel.py 纯函数，互相关粗对齐加逐块能量门，不引入重依赖。
- 接入：按键开始后台启动参考采集，结束时收 PCM 进 pipeline，转写前消除，失败回退原声。
- 开关：菜单栏输入偏好系统音频参考消除一项，配置 ref_cancel 持久化。
- 实测：播放音乐按住不说话判 no_speech，suppressed 0.7 以上；说话段正常转写，RTF 约 0.1。

旧 Tier 1 设想的 VPIO/AEC 未采用：本机播放的是第三方音乐，不是自身引擎播放，voice processing unit 消除不了外部音乐。

