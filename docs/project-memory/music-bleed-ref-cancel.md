# 扬声器音乐串扰与系统音频参考消除

用户实测确认：内置扬声器播放音乐或视频人声时，串入内置麦克风，明显降低转写成功率。

## 维护禁区（2026-09-13 真机验收后冻结）

2026-09-13 用户确认「现在可以了」。这条链路是稳定架构，不是试验田。

不要在没有新的真机失败证据时改这些边界：

- 不要改回按键时冷启动 / 停掉 ScreenCaptureKit。第二次建流会吐全零。
- 不要改回块级能量门控或单增益时域相减。视频人声会漏进 Whisper。
- 不要先峰值归一化再消除，也不要把残差峰值拉满。
- 不要用 `CMSampleBufferGetDataBuffer` 取 PCM，不要 L+R 平均。
- 不要为假设场景引入重依赖或 VPIO。

要动 `core/sysref.py`、`core/sysref_capture.swift`、`core/ref_cancel.py` 或 pipeline 接入顺序，先读本文再改，改完必须真机：扬声器放人声视频，连录两次，第二次 `ref_rms` 不能为 0。

## 调研结论

- FFT 谱降噪对音乐/人声串音基本无效。
- whisper-server 没有可直接解决该问题的降噪开关。
- sounddevice/PortAudio 不能直接打开 macOS Voice Processing。
- VPIO/AEC 未采用：本机播放的是第三方内容，不是自身引擎播放。
- 录音中改系统音量（ducking）会打断用户，已删除。

## 当前方案（2026-09-13 重构）

常驻采集 + 频域维纳相减。不要每次按键冷启动 ScreenCaptureKit。

- 采集：开关打开且当前是扬声器时，Swift helper `core/sysref_capture` 预热并一直跑。录音只 `begin_segment`/`end_segment` 切段，带 250ms 预滚。构建见 `core/SYSREF_BUILD.md`。
- 取声：AudioBufferList 第一声道。不要 `CMSampleBufferGetDataBuffer`（macOS 26 上常是空缓冲），不要 L+R 平均（立体声反相会抵成静音）。
- 消除：`core/ref_cancel.py`。互相关对齐，STFT 平滑维纳，高相干帧再压残差。在峰值归一化之前做；残差峰值低于 0.02 不再拉满。
- 失败回退原声。耳机输出不预热、本段跳过（建流会让蓝牙音乐顿一下）。
- 开关：菜单栏「系统音频参考消除」，`ref_cancel` 持久化。DEV 与正式版权限隔离。

## 已踩过的坑

- 按键冷启动：ready 约 200–400ms，第二次建流经常 8 秒全零。
- 块级能量门 + 峰值归一化：视频人声衰减后仍会被 Whisper 转写，残差还会被拉满。
- 单增益时域相减：扬声器到麦是有色通路，gain≈0.08 消不掉人声（2026-09-13 实测 erle 不足，`text_len=13/48`）。
- 时延搜索只有 200ms：估到窗口边界，对不齐。
