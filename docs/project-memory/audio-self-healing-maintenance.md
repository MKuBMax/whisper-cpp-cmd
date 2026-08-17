# 音频自愈系统维护禁区

当前音频自愈系统包含：子进程隔离、三信号检测、respawn 退避、HALF_OPEN、warmup 和虚拟设备预过滤。

## 持续检测

- reader 监测录音中的 PCM 停滞约 2.5 秒和 stdout EOF。
- ping 线程在非录音状态每 8 秒探测 worker。
- 每条命令有独立 ack 超时。
- degraded 后每 60 秒进入 HALF_OPEN 试探。
- respawn 后新 worker 前 5 秒跳过 ping，避免把慢启动误判为故障。
- audio_worker.py 记录 Pa_OpenStream 耗时。

## 维护禁区

- respawn 只能由发送命令的调用线程执行。ping、reader 和 HALF_OPEN 线程只能标记 worker dead，不能直接调用 respawn，否则可能重入死锁。
- 新增 worker 命令调用者必须经过已有的 _send_cmd_once 和 stdin lock，不能直接写 proc.stdin。
- 给 AudioSource 增加字段时，要同步更新 tests/test_audio_source_client.py 的 _make_client。
- 不要在应用侧做重采样，除非新的数据明确证明有收益；当前项目没有相应重采样依赖，质量风险高。
- 不要引入违反本地优先约束的重依赖。

## 排查路径

双日志一起看：

- logs/whisper-audio-worker-*.log：worker 侧 open、abort、close 和耗时。
- logs/whisper-cpp-cmd.log：client 侧 respawn、HALF_OPEN、idle ping 和 degraded。

EOF 要结合 proc.poll() 区分 worker 崩溃、正常退出和 native 卡死；出现频繁 idle ping timeout 时，优先检查 generation 守卫是否被破坏。

