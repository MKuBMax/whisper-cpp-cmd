# 重启后的验证

运行 restart_app.sh 或 ship_app.sh 后，不能立刻判断成功。

必须等待约 10–15 秒，因为 App 启动需要加载模型、构建 pipeline 和启动后端。

验证：

~~~sh
ps aux | grep "WhisperCppCmd.app/Contents/MacOS"
tail -n 80 logs/whisper-cpp-cmd.log
~~~

进程应当存活，日志尾部应出现“应用已启动，进入事件循环”。只看到进程短暂存在，或在模型加载完成前检查，都不能算重启成功。

