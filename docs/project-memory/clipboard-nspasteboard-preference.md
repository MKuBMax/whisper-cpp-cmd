# 剪贴板：NSPasteboard 优先

core/clipboard.py 的 copy() 必须优先走 NSPasteboard，pbcopy/pbpaste 子进程只能作为兜底。

原因是 GUI App 内 pbcopy 后立刻校验存在 pasteboard 同步竞态，历史实测约 45% 校验失败；NSPasteboard 实测 100% 可靠且没有子进程开销。

insert() 会调用 copy()，所以 quick 模式听写也会受到这条路径影响。修改复制逻辑时不要把 pbcopy 调回主路径。

