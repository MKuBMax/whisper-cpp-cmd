# 音频卡死根因：僵尸 AudioUnit

2026-07-04 的全样本日志统计确定了录音重启卡死的因果链：

1. 录音停止时 Pa_StopStream 或 abort 偶发超过 3 秒。
2. 兜底的 abandon-abort 和 abandon-close 也可能各自卡住。
3. close 没有完成，僵尸 AudioUnit 泄漏在当前进程。
4. 后续 Pa_OpenStream 在同一进程内必然卡死，直到进程退出。

8 次 open 卡死样本全部有前序僵尸 AudioUnit，没有“无僵尸但 open 独立卡死”的样本。虚拟设备是最强的系统级远因，但不是直接近因；修复重点应该切断“僵尸到后续 open”的传播。

## 当前修复方案

采集内核已经隔离进子进程：

- core/audio_worker.py 负责采集内核和 stdin/stdout IPC。
- core/audio_source.py 对外接口保持不变，作为 IPC client。
- 命令 ack 超时、录音中的 PCM stall 和 stdout EOF 会触发 worker 失效。
- respawn 通过 SIGTERM 到 SIGKILL 清理旧 worker，generation 计数让旧 reader 静默退出。
- 60 秒窗口内限制 respawn 次数，避免风暴。
- core/process_guard.py 回收启动期孤儿 worker。

## 为什么不在进程内 re-init

- sounddevice 的 terminate/initialize 无法安全释放卡住的旧 InputStream。
- 旧 InputStream 引用可能变成悬垂指针，碰触后存在段错误风险。
- Pa_Terminate 自身也可能卡在同一个 CoreAudio 调用。
- always-running 会让 macOS 麦克风指示灯常亮，违反按需激活。

worker 必须使用 .venv-arm64/bin/python；py2app alias 下主进程的 sys.executable 可能是没有项目依赖的系统 Python。

