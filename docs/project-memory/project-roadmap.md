# 路线图与明确不做项

## 当前状态

P0 地基已经完成并经过真机验证，包括：

- 删除死代码。
- 输出原子写入。
- pytest 基础设施。
- watchdog 超时统一和自愈。
- 录音时长上限。
- 音频设备热插拔容错。
- 睡眠/唤醒感知。
- NSPasteboard 优先的剪贴板路径。

P2 中以下能力已经合并到 main：

- 可配置热键。
- 录音浮窗。
- perf.jsonl 性能记录。
- VAD 和 whisper-server 进程清理。
- controller 显式状态机。
- 音频采集子进程隔离和自愈。
- 录音 ducking。

识别准确率基础增强已落地：VAD 之外的数字静音/空输出保护。默认识别语言保持中文；需要多语言识别时由用户显式选择。置信度不会作为听写输出的拦截条件。

## 当前待办

- [x] 统一本机与分发打包流程：`build_app.sh`/`package_app.sh` 生成并验证
  standalone App/zip，`ship_app.sh` 负责替换 `/Applications` 并重启；本机不再
  保留或启动项目根目录的 alias App。

## 初版同事分发已完成

当前基础分发目标是 Apple Silicon 同事之间共享 standalone zip：

- package_app.sh 使用 py2app standalone 模式构建，不依赖同事电脑上的 Python、Homebrew 或项目源码。
- whisper-cli、whisper-server 及其 arm64 动态库随 App 提供。
- Whisper 模型不随包提供，由使用者自行下载到 ~/Library/Application Support/WhisperCppCmd/models/。
- 配置、历史、术语表和日志写入用户 Library，不写入 .app。
- 默认测试包使用 ad hoc 签名，只作为内部早期分发；配置 Developer ID 和 notarization profile 后可构建正式签名公证包，构建目标暂限 Apple Silicon。
- 更新安装只对 standalone 包开启：下载后的 GitHub zip 会做 HTTPS 主机、解压路径、App 结构和签名校验；当前 App 退出后由独立 helper 替换，保留 `.previous`，启动失败时保留 `.failed.*` 并恢复旧版本。

## 发布体验已接入

- 首次启动向导即使在模型尚未下载时也能显示；放入模型后可以从菜单栏重新加载。
- 统计面板读取本地 perf.jsonl；更新检查只连接 GitHub Releases，默认每天后台检查一次，安装前验证更新包签名。
- package_app.sh 默认生成 ad hoc 签名包，也支持通过 Developer ID identity、notarytool profile 和 `WHISPER_CPP_CMD_NOTARIZE=true` 构建签名公证包。

## 菜单栏开机启动已完成

- 菜单栏提供「开机启动」勾选项。
- macOS 13+ 使用 `SMAppService.mainApp` 注册到系统登录项，不需要管理员权限；旧系统回退到当前用户的 `~/Library/LaunchAgents/com.mkbm.whispercppcmd.plist`。
- 勾选在下次用户登录时生效，取消只删除该用户级启动项，不影响当前 App。

## 全局热键权限已按 macOS 两层权限处理

- `pynput` 的 listen-only `CGEventTap` 在当前 macOS 上由「辅助功能」授权即可建立；「输入监控」仍单独检查并提供状态，不能把 `CGPreflightListenEventAccess()` 的结果直接当成监听器能否启动的硬门槛。
- App 启动和菜单打开时分别检查两项权限；输入监控请求使用 `IOHIDRequestAccess()`，系统不弹窗时打开对应的 System Settings 页面。
- 用户在系统设置中完成授权后，App 通过 1 秒 signal pump 自动重建全局热键监听器，不要求再次重启。
- 未签名/未公证的 ad hoc App 在重新构建或替换后，macOS 可能把权限记录视为不同实例；验证时必须对当前 `/Applications/WhisperCppCmd.app` 重新确认两项权限。
- 权限未完成时显示友好向导页，持续刷新每项权限状态并支持关闭/稍后设置，菜单栏提供常驻入口；不再使用强制阻塞弹窗。
- 欢迎页支持按 ESC / Cmd+W 随时关闭，允许被正常覆盖，不阻塞用户日常工作。
- 菜单栏状态项使用系统标准 `NSVariableStatusItemLength` 并配置 `autosaveName` 持久化，由 AppKit 统一管理，避免高频自诊断与重建造成图标闪烁或丢失。
- 发现权限或监听器异常时只引导用户在系统设置页面手动清理并重新添加当前 App，不调用 `tccutil reset`，不按名称匹配或删除旧 TCC 条目。

## 后续公开分发时再考虑

- 完整 CI 构建管线和更新包发布自动化（不引入使用遥测）；本地 standalone 更新的失败回滚流程已经落地。
- macOS 原生通知和更完整的发布自动化。
- Intel/Universal 架构。
- npm/Brew 等渠道。

纯自用场景不需要为了分发提前做这些工作。

## 明确不做

- CoreML：当前 whisper.cpp 构建链不支持且收益不明确。
- 音频流式上传。
- 强制额外本地服务。
- 模型完整性魔数校验、磁盘满预检等低概率复杂防御。
- 常驻 VAD 录音：会让麦克风指示灯常亮，违反按需激活。
- 默认云端 LLM Polish。
- 会议分离、双通道系统音频和多引擎维护。
- 模型微调：whisper.cpp 是推理引擎，不提供项目所需的微调链路。
