# PyObjC GUI 的 signal pump

在 PyObjC + AppHelper.runEventLoop()/NSApp.run() 的 GUI App 中，signal.signal() 注册的 Python handler 在主线程阻塞于 NSRunLoop 时不会及时执行。

原因是 NSRunLoop 在 C 层循环，主线程没有回到 Python bytecode，CPython 的 pending signal 标志就不会被处理。SIGTERM 可能延迟数十秒，用户容易改用 SIGKILL，进而留下子进程。

## 当前方案

使用一个约 1 秒重复触发的 NSTimer，target 是 NSObject，selector 只做空操作。这个 timer 让主线程定期回到 Python，SIGINT/SIGTERM/SIGHUP 就能及时进入 graceful shutdown。

注意：

- SIGKILL 和崩溃仍然无法被优雅处理。
- macOS 没有 Linux 的 PR_SET_PDEATHSIG，子进程退出兜底仍需依靠 process guard 和 worker 隔离。

