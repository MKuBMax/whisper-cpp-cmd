# 简约语音输入重构与问题审计

2026-09-05，检查基线 `0fe88b7`，开始时工作区干净。产品目标以用户本次决定为准：本地优先，下载模型，按住右 Command 录音、松开识别；可编辑光标处输入，否则复制并短暂提示。采用原生 macOS 界面。

## 已确认的问题

| 问题 | 证据与影响 | 本次处理 |
| --- | --- | --- |
| 菜单栏不可见的补丁方向错误 | 9 月 2–4 日日志存在 `isVisible=True` 但 frame 在屏幕外；新提交用状态文字增加宽度，无法解决系统空间不足 | 固定 28 pt 模板图标，稳定 autosaveName；保留 Dock 重开入口，不再暴露反复重挂和加宽开关 |
| 三个窗口重复职责 | 欢迎、控制中心、设置分别构建控件、状态和入口；设置窗口 540 pt，复选框从 x=52 延伸 650 pt | 合并成一个固定大小的原生窗口，首次设置和日常管理共用 |
| 引导完成不代表能用 | 欢迎页只检查权限，不检查模型和监听器；稍后设置又打开仪表盘 | 统一显示模型、必需权限及监听器状态；关闭只关闭 |
| 缺少 App 内模型下载 | 需要前往网页、辨识 GGML 文件名、手动放入目录和重载 | 推荐 Turbo q5 模型后台下载，支持进度、取消、失败重试，下载完成后加载 |
| VAD 被列为识别模型 | `list_available_models()` 将所有 ggml-*.bin 列出，包含 Silero | 排除 Silero、空文件与下载中的 .part |
| 启动与模型重载阻塞 UI | 初始化先同步建管线、载模型，然后创建菜单；加载失败会退出 | 先启动原生界面和事件循环，再后台加载模型；失败可留在界面处理 |
| 常驻胶囊卡在转写中 | `_set_state` 的所有非录音状态调用 `_hide_overlay`，该函数又调用胶囊的 `on_recording_stopped`，会把 idle/error 改回 processing | 停用常驻胶囊，只保留录音浮窗与短暂结果提示 |
| 默认实时预览偏离目标 | 默认 preview，会在录音过程中改写当前输入内容 | 现有配置迁移到 quick，App 固定松开后交付最终文本 |
| 无光标仍输入 | Clipboard.insert 按应用名称选择逐字事件或粘贴，未先确认可编辑控件；发出事件即返回 True | 录音时记住可编辑目标；输出前复核目标，不可确认、目标切换、密码框均只复制 |
| 多路径可能重复输入 | Unicode typing、CGEvent、AppleScript、AX 多条链路无法验证是否已接受文本 | 正常最终交付仅保留一次粘贴请求，不把事件发送称为输入已验证 |
| 松开后麦克风仍录音 | release 先等待 backend warmup，之后才 stop_recording | 在 pipeline 停止采集后、转写前等待 warmup |
| 转写时积压新录音 | 按键事件无 processing/repeat 防护，可排队后延迟录音 | busy/processing/已有按下事件期间不接收新录音 |
| 中文脚本菜单始终禁用 | 真实 App 菜单验收发现简繁选项全灰；基线绑定 `selectChineseScript:` 但没有实现 selector | 补齐菜单回调 |
| 最近结果混入状态文案 | `_last_result` 同时存模型加载提示、录音错误和转录文本；复制最近结果可能复制错误提示 | 独立保存最近成功转录文本 |
| 下载依赖本机 Homebrew 证书 | OpenSSL 默认根证书位于 /opt/homebrew/etc，其他 Mac 不一定存在 | 模型下载使用 macOS 自带 /etc/ssl/cert.pem，并验证官方 URL 返回 HTTP 200 |

## 参考项目与取舍

- [Handy](https://github.com/cjpais/Handy)：参考其单一任务、离线识别、按住录音与模型管理的产品组织方式。保留本项目既有隔离音频采集，不引入 Tauri/Rust 重写。
- [VoiceInk](https://github.com/Beingpax/VoiceInk)：参考原生 macOS 语音输入及权限设置的呈现方式。不引入云端文本增强、多模式或订阅功能。
- [PyObjC 原生 App 示例](https://github.com/ronaldoussoren/pyobjc/tree/main/pyobjc-framework-Cocoa/Examples)：使用 AppKit 生命周期、NSWindow、NSMenu、NSStatusItem；窗口不使用模态事件循环。
- [模型来源](https://huggingface.co/ggerganov/whisper.cpp)：推荐 `large-v3-turbo-q5_0`，现有 large-v3 文件仍可使用。

## 保留边界与需实测项目

- 音频 worker 隔离、generation、防重入、respawn 线程边界不变。
- 录音浮窗沿用真实 App 验证过的 alpha 半透明方案。本次没有声称实现 NSGlassEffectView 系统 Liquid Glass。
- macOS 权限必须由使用者允许；模型文件是额外下载项，权限不是能绕过的安装步骤。
- 系统或第三方菜单栏工具隐藏图标、菜单栏空间不足，不能仅靠 setVisible 保证解决；必须看实际 UI，Dock 提供恢复入口。
- 某些自绘编辑器不完整提供 Accessibility 属性，会保守复制；发出一次 Cmd+V 也无法普遍证明对方 App 已插入。
- 完整下载、真实麦克风音质、右 Command 物理按住/松开和不同编辑器输入需要分别实测，不能用单元测试替代。

## 最终安装版冒烟记录

- 2026-09-05 最终 standalone 包已替换 `/Applications/WhisperCppCmd.app` 并启动；主进程和 audio worker 持续存活，日志出现「应用已启动，进入事件循环」，随后 `large-v3` 的 whisper-server 完成加载。
- 主窗口已在真实 App 中打开，单窗口文案、模型下拉框、下载按钮、权限状态和关闭入口均可见；没有再创建常驻桌面胶囊。
- 关闭窗口动作已在最终安装版中验证：窗口关闭后 `CGWindowList` 不再列出 WhisperCppCmd 窗口，但主进程和 audio worker 仍存活；重新打开后只有一个主窗口，没有常驻胶囊窗口。`Cmd+W` 同样已验证能关闭窗口。
- 输入偏好菜单的无障碍树已显示麦克风、语言、中文脚本和快捷键子菜单；简体、繁体、自动三项均存在，中文脚本菜单不再全灰。
- 菜单栏状态项日志显示 `visible=True`、`button_hidden=False`、按钮宽度 28 pt。状态栏窗口的内部 frame 不能证明图标在屏幕上，菜单栏图标仍需在用户自己的菜单栏环境中亲眼确认。
- 当前测试环境的麦克风和辅助功能权限仍未授权，因此没有把右 Command 录音、真实光标输入或剪贴板回退标记为已验收；授权动作需要用户在系统设置中完成。
