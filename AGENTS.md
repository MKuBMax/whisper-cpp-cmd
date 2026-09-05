# 项目约定

- 在开始新的开发任务前，先检查 `git` 提交历史和当前工作区状态。
- 优先查看 `git status --short` 和最近的 `git log --oneline`，必要时再看相关 `git show`。
- 这些提交通常包含已有背景、决策记录和未完成工作，先看再动手。

## 项目记忆

- 本项目的长期背景、已验证结论和维护禁区见 [docs/project-memory/README.md](docs/project-memory/README.md)；开始涉及音频、浮窗、运行时稳定性或产品路线的任务前，先读索引并按需读取对应专题。
- 稳定规则放在本文件；专题文档记录决策背景。代码、测试和最新用户决策优先于旧记忆，做出新决策后同步更新专题文档。
- 项目坚持本地优先、隐私、单用户、单人维护和最小可回滚改动，避免为了假设性场景引入重依赖或过度抽象。
- 本项目 Python 运行、测试和冒烟检查使用 .venv-arm64/bin/python，不要切换到没有项目依赖的 Conda 环境。
- GUI 内剪贴板优先使用 NSPasteboard；新增 GUI 子进程读取时必须显式指定 UTF-8，避免无 TTY 进程退化为 ASCII。
- 音频采集的子进程隔离、自愈、generation 防护、stdin 锁和 respawn 调用边界属于稳定架构；修改前先读音频专题，不能随意改回进程内重置。
- 录音浮窗的系统材质已经在真实 app 进程中验证不可用；当前方案是纯 alpha 半透明皮肤，若再改外观必须在真实 app 中验收。

## 开发与部署工作流

- **默认处于开发模式。** 修改代码、运行测试、调试和让用户试用时，只使用 `bash run_dev.sh`；不得调用 `ship_app.sh`、`build_app.sh`、`package_app.sh` 或替换 `/Applications/WhisperCppCmd.app`。
- **只有用户明确表示“准备发布/发布/安装正式版”时，才进入发布模式。** 发布模式才允许运行 `bash ship_app.sh`，用 standalone 包替换 `/Applications/WhisperCppCmd.app` 并重启。
- 用户说“继续开发”“试一下”“验证样式/功能”均属于开发模式，不等同于发布授权。
- **重启后必须等待 10-15 秒**，再检查进程是否存活：`ps aux | grep "WhisperCppCmd.app/Contents/MacOS"`，并确认日志尾部出现「应用已启动，进入事件循环」。app 启动要加载模型、建 pipeline，过早检查会误判「重启成功」。
- 含代码改动的提交也不自动触发发布；只有用户明确授权发布时才 ship。纯文档改动无需 ship。
- `run_dev.sh` 直接使用 `.venv-arm64/bin/python` 运行源码，不替换正式 App；`ship_app.sh` 是发布动作，不是普通重启或审核动作。
