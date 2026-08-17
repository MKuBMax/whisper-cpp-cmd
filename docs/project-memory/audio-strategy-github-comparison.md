# 音频自愈策略对比

当前的“音频采集子进程隔离 + 自愈”策略已经覆盖公开竞品常见的恢复能力，并且解决的是 sounddevice/PortAudio 在 CoreAudio 上的进程级爆炸半径问题。

## 不要破坏的优势

- 进程级隔离，旧 AudioUnit 随 worker 退出由系统释放。
- generation token，避免旧 reader 把新 worker 状态误报。
- 三个正交失效信号：命令 ack 超时、录音 PCM stall、stdout EOF。
- 回调不直接调用 Pa_*。
- 启动期孤儿回收。

## 已经补齐的自愈能力

- respawn 退避和 jitter。
- 空闲态周期 ping。
- worker 稳定运行后重置 respawn 计数。
- degraded 后的 HALF_OPEN 自动试探恢复。
- 新 worker 启动宽限。
- 真实设备优先、虚拟设备降权。
- EOF 按退出码区分崩溃、正常退出和仍存活但卡死。
- Pa_OpenStream 耗时日志。

## 已否决的采样率方向

曾经怀疑固定 16 kHz open 会放大 CoreAudio 重配置竞态，但 250 个 open 样本的 p95 约 38.2 ms，超过 100 ms 的样本为零；唯一真实卡死是进程级 start ack 超时，不是慢 open。因此不做应用侧重采样，不引入 scipy/librosa，也不改转写通路。

