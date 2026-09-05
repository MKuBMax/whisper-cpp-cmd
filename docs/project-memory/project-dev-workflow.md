# 开发与部署流程

## 模式边界

项目有两个互斥工作模式。默认是开发模式。

### 开发模式（默认）

用于改代码、跑测试、调试和让用户试用：

```sh
bash run_dev.sh
```

开发模式直接运行 `.venv-arm64/bin/python` 的源码，不构建、不签名、不替换 `/Applications/WhisperCppCmd.app`，因此不会因每次调试重装 App 而反复触发 macOS 权限授权。

### 发布模式（需要用户明确授权）

只有用户明确说“准备发布”“发布正式版”或“安装正式版”后，才运行：

```sh
bash ship_app.sh
```

这会构建 standalone 包、替换 `/Applications/WhisperCppCmd.app` 并重启。用户说“继续开发”“试一下”“验证样式/功能”都不构成发布授权。

## 本机审核与部署

进入发布模式并得到用户明确授权后运行：

~~~sh
bash ship_app.sh
~~~

这一步通过 `build_app.sh`/`package_app.sh` 生成 standalone 包，替换 `/Applications/WhisperCppCmd.app` 后启动，适合让用户审核当前实现；本机流程不保留或启动项目根目录的 alias 包。

如果只需要生成 standalone 分发包、不替换当前安装，可运行：

~~~sh
bash build_app.sh
~~~

`restart_app.sh` 默认只重启 `/Applications/WhisperCppCmd.app`，不再启动 alias 包。

## 提交阶段

代码提交、合并或重启不会自动进入发布模式。除非用户明确授权发布，否则继续使用开发模式。

纯文档或非代码改动，例如 AGENTS.md、README 和本目录记忆文档，不需要 ship。

## 纪律

代码改动完成后在开发模式让用户审核；用户明确准备发布后才 ship。发布重启后等待 10–15 秒，再检查进程和日志。

## standalone 发布

正式分发使用仓库根目录的 `VERSION` 和项目 `.venv-arm64/bin/python`，通过
`bash package_app.sh` 生成 Apple Silicon standalone zip。默认使用 ad hoc
签名；配置 `WHISPER_CPP_CMD_SIGNING_IDENTITY`、
`WHISPER_CPP_CMD_NOTARY_PROFILE` 和 `WHISPER_CPP_CMD_NOTARIZE=true` 后才提交
Developer ID notarization。更新 helper 位于 App 的 `Contents/Resources/update_app.sh`，
只在 standalone 包中运行，并在当前 App 退出后执行替换和失败回滚。
