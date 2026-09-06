# 扬声器音乐串扰与系统音频参考消除

用户实测确认：内置扬声器播放音乐时，音乐会串入内置麦克风，明显降低转写成功率。

## 调研结论

- FFT 谱降噪对音乐基本无效；它主要处理稳态噪声底，音乐调性信号会被保留。
- whisper-server 没有可直接解决该问题的降噪开关。
- 普遍的听写工具靠 raw 采集和戴耳机规避。
- sounddevice/PortAudio 不能直接打开 macOS Voice Processing。

## 已落地的方案：系统音频参考消除（ref_cancel，默认关闭）

录音时经 ScreenCaptureKit 抓系统输出做参考，对齐后块级门控压制扬声器串音。
录音全程不改系统音量。

- 采集：Swift 子进程 core/sysref_capture 经 SCStream 只抓音频，输出 16kHz 单声道 PCM，构建见 core/SYSREF_BUILD.md。
- 消除：core/ref_cancel.py 纯函数，互相关粗对齐加逐块能量门，不引入重依赖。
- 接入：按键开始后台启动参考采集，结束时收 PCM 进 pipeline，转写前消除，失败回退原声。
- 开关：菜单栏输入偏好系统音频参考消除一项，配置 ref_cancel 持久化。
- 实测：播放音乐按住不说话判 no_speech，suppressed 0.7 以上；说话段正常转写，RTF 约 0.1。

旧的录音压低系统音量方案已彻底删除：录音中改系统音量会打断用户听音乐，
与本地优先的体验冲突。VPIO/AEC 未采用：本机播放的是第三方音乐，不是自身引擎播放，
voice processing unit 消除不了外部音乐。
