# 开发与部署流程

## 本机审核与部署

代码改完后运行：

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

只要提交或合并包含代码改动，就必须运行：

~~~sh
bash ship_app.sh
~~~

该脚本会调用 `build_app.sh`/`package_app.sh` 构建并验证 standalone 包，替换 /Applications/WhisperCppCmd.app 并重启。代码提交后不能用普通重启代替 ship。

纯文档或非代码改动，例如 AGENTS.md、README 和本目录记忆文档，不需要 ship。

## 纪律

代码改动完成后先 restart 让用户审核；用户确认后再提交；含代码提交完成后立即 ship。每次重启都要等待 10–15 秒，再检查进程和日志。

## standalone 发布

正式分发使用仓库根目录的 `VERSION` 和项目 `.venv-arm64/bin/python`，通过
`bash package_app.sh` 生成 Apple Silicon standalone zip。默认使用 ad hoc
签名；配置 `WHISPER_CPP_CMD_SIGNING_IDENTITY`、
`WHISPER_CPP_CMD_NOTARY_PROFILE` 和 `WHISPER_CPP_CMD_NOTARIZE=true` 后才提交
Developer ID notarization。更新 helper 位于 App 的 `Contents/Resources/update_app.sh`，
只在 standalone 包中运行，并在当前 App 退出后执行替换和失败回滚。
