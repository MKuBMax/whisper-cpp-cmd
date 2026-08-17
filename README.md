# WhisperCppCmd

一个本地 macOS 语音输入工具：

- 按住 **右 Command**
- 录音并转写
- 松开后把文本输入到当前光标位置

当前项目已经从“终端里运行的命令行工具”改成了**可双击启动的菜单栏 App**。

---

## 这次改了什么

### 1. 从命令行模式改成真正的 `.app`

之前的启动方式本质上还是外部 Python / shell 在跑，所以系统权限提示里看到的是 `python3.12`，不是 App 本身。

现在改成：

- `py2app` 打包
- `app_bootstrap.py` 作为 App 入口
- `build_app.sh` 统一构建
- `setup.py` 定义 App bundle 信息和权限描述

这样做之后：

- 麦克风权限归属 `WhisperCppCmd.app`
- 辅助功能/自动化权限也更容易归属到 App
- 可以直接双击启动

> 当前使用的是 **py2app alias 模式**，只适合这台电脑、这个项目目录下的本地使用，不是可分发安装包。

---

### 2. 保留双击启动后的调试输出

命令行模式的一个优势是“能看到输出，方便调试”。

为了保留这个能力，App 启动后会把输出写到日志文件：

- `logs/app-launcher.log`
- `logs/whisper-cpp-cmd.log`

这样即使双击启动，也还能看完整调试信息。

### 3. 菜单栏开机启动

菜单栏中新增「开机启动」开关。macOS 13+ 勾选后会通过系统原生登录项服务注册，
并显示在“系统设置 → 通用 → 登录项”中；旧系统才回退到
`~/Library/LaunchAgents/`。再次点击即可取消。这个开关不会立即重启或关闭当前
App，移动 App 后需要重新打开 App 并重新勾选来更新路径。

---

### 3. 修复“能识别但不能稳定输入到光标处”的问题

这次踩到的核心坑有两个：

1. `osascript` / `System Events` 返回成功，**不代表目标输入框真的收到了粘贴**
2. Accessibility AX 写值在很多地方会“看起来成功”，但实际：
   - 浏览器网页内输入框不稳定
   - 终端类控件不稳定

所以现在的插入策略改成了更偏“真实用户输入”的方式：

#### 当前插入优先级

1. **iTerm2**
   - 直接走 iTerm2 自己的 `write text`

2. **浏览器 / 终端类应用**
   - 优先走 `CGEvent` 的 **Unicode 键盘输入**
   - 更接近真实键盘事件

3. **其他普通应用**
   - 先尝试 `CGEvent` 粘贴
   - 再回退到 `System Events` 粘贴

4. **最后兜底**
   - 仅对非浏览器/非终端类控件再尝试 AX 直接写值

#### 为什么这样更稳

- 浏览器网页输入框、终端区往往更接受“真实键盘输入”
- `System Events` 更像“发出一个粘贴命令”，但不保证命令真的落到正确控件
- AX 对 DOM / 终端控件容易出现“日志成功、体感失败”

这也是为什么最终修复后，Safari/Opera 网页输入框、浏览器地址栏、iTerm2 等场景都能稳定工作。

---

## 关键文件

### 打包相关

- `build_app.sh`
  - 本机构建脚本
  - 负责生成图标和执行 `py2app`

- `setup.py`
  - App bundle 配置
  - 包含名称、bundle id、权限描述等

- `app_bootstrap.py`
  - App 启动入口
  - 重定向 stdout/stderr 到日志
  - 切回项目根目录后调用 `main.main()`

### 输入相关

- `core/clipboard.py`
  - 当前最关键的输入策略实现
  - 包含：
    - 剪贴板复制
    - CGEvent 粘贴
    - System Events 粘贴
    - Unicode 键盘输入
    - iTerm2 专用输入
    - AX 兜底

- `app/controller.py`
  - 把配置里的 `paste_delay` 接入流水线
  - App 启动时检查辅助功能与输入监控权限；缺失时请求 macOS 系统授权引导，已授权时不弹窗
  - 菜单栏分别显示两项权限状态；输入监控未授权时点击该项会请求权限并打开对应系统设置页面

---

## 如何构建

使用本机 conda 环境：

```bash
./build_app.sh
```

生成结果：

```bash
/Users/mkbm/work/app/whisper-cpp-cmd/WhisperCppCmd.app
```

---

## 给同事的基础分发包

现在可以在一台 Apple Silicon Mac 上直接构建 standalone zip：

    bash package_app.sh

构建机需要已有项目的 .venv-arm64 和 Homebrew 的 whisper-cpp；这些只是构建依赖，不需要同事安装。结果在：

    release/WhisperCppCmd-macOS-arm64.zip

这个 zip 已包含 Python 依赖、whisper-cli、whisper-server 和动态库，但不包含体积很大的 Whisper 模型。同事解压后先运行 Prepare WhisperCppCmd.command，把模型放进 ~/Library/Application Support/WhisperCppCmd/models/，再双击 WhisperCppCmd.app。详细步骤见 zip 内的 README.md。

当前分发目标是 Apple Silicon；包尚未使用 Apple Developer ID 签名和 notarize，首次打开可能需要先尝试打开 App，再到“系统设置 → 隐私与安全性”中点击“仍要打开”。

## 如何启动

直接双击：

```bash
WhisperCppCmd.app
```

或者在 Finder 里打开项目目录后双击 App。

---

## 首次权限

首次使用时，通常需要允许：

- **麦克风**
- **辅助功能**
- 视系统表现可能还需要 **自动化**

如果右 Command 没反应，先检查：

`系统设置 → 隐私与安全性`

里面至少确认：

- 麦克风
- 辅助功能
- 输入监控

App 启动时会自动检查辅助功能与输入监控权限；菜单栏会分别显示两项“已允许/未允许”状态。输入监控未允许时，点击该项会请求 macOS 授权，并在系统没有弹窗时自动打开“输入监控”设置页。若权限状态或监听器状态异常，App 会弹出明确的手动清理和重新添加指引，不会删除或重置系统权限记录。辅助功能授权完成后 App 会自动重建全局热键监听器，无需手动重启。macOS 的最终授权必须由用户确认，App 不能静默替用户开启。

---

## 调试

查看最近日志：

```bash
tail -n 120 logs/app-launcher.log
tail -n 200 logs/whisper-cpp-cmd.log
```

重点看这些日志标记：

- `mode=iterm2_write_text`
- `mode=unicode_typing`
- `mode=cgevent_fallback`
- `mode=applescript`
- `mode=ax_selected_text`
- `mode=ax_value`

---

## 当前已验证结果

已经确认可在多种场景下成功输入，包括：

- iTerm2 终端区
- 浏览器地址栏
- 浏览器网页内部输入框
- 各类普通输入框

---

## 隐私边界

WhisperCppCmd 是**本地优先**工具——音频和文本全程不出设备：

| 数据 | 离开设备？ | 说明 |
|---|---|---|
| 录音音频 | 否 | sounddevice 录音 → 本地 wav → whisper-cli/whisper-server（本机二进制）转写，全程本机内存/磁盘 |
| 转写文本 | 否 | 本地 whisper 转写 → OpenCC 简繁归一（本地）→ 插入光标 / `history.json`（本地） |
| whisper 模型 | 否 | `models/` 本地存放（large-v3 / large-v3-turbo / q5_0，用户自行放置） |
| Silero VAD 模型 | 仅首次 | 首次启用 VAD 时从 HuggingFace 下载 `ggml-silero-v6.2.0.bin` 到 `models/`，之后全本地；可在设置关 VAD |
| 遥测 / 统计上报 | 无 | `perf.jsonl` / `history.json` 纯本地，不上传 |
| 云端 LLM 后处理 | 无 | 明确不做，所有后处理是本地确定性变换 |
| 自动更新检查 | 无 | 不联网 |

**明确不做**（守住本地优先 + 按需激活麦克风）：CoreML、音频流式上传、强制 ollama/LLM、常驻 VAD 监听（麦克风指示灯不常亮）。

排查用日志都在本机：`logs/whisper-cpp-cmd.log`（client）+ `logs/whisper-audio-worker-*.log`（采集 worker）+ `logs/whisper-server-*.log`（转写）。

---

## 备注

- build_app.sh 仍是本机审核用的 py2app alias 构建，不要直接把它生成的 App 发给同事
- package_app.sh 是当前最基础的 standalone 分发入口
- 后续再考虑正式签名、公证、Intel/Universal 架构、自动更新以及 npm/Brew 渠道
