# GUI 子进程 UTF-8 坑

WhisperCppCmd 是从 LaunchServices 启动的 py2app GUI，通常没有 TTY。在这种环境里，subprocess.run(..., text=True) 的默认解码可能退化为 ASCII。

只要子进程输出包含中文设备名、中文命令行或中文剪贴板内容，就可能触发 UnicodeDecodeError。很多调用被 broad except 吞掉后，会表现为功能静默失效。

## 新增 subprocess 的规则

优先使用：

~~~python
subprocess.run(..., encoding="utf-8", errors="replace")
~~~

或者使用 bytes，并显式调用 decode("utf-8")。

已盘点并修复的重点区域包括 process_guard.py、media_ducker.py 和 clipboard.py。模型进程把输出重定向到文件的 text=True 不经过 Python 解码，不属于同一风险。

看到 UnicodeDecodeError: 'ascii' codec 时，优先排查真实 GUI 进程，而不是只在终端里复现；终端环境通常无法复现该问题。

