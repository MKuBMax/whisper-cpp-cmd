# 开发与部署流程

## 审核阶段

代码改完后运行：

~~~sh
bash restart_app.sh
~~~

py2app 使用 alias 模式，重启即可加载源码变化，适合让用户审核当前实现。

## 提交阶段

只要提交或合并包含代码改动，就必须运行：

~~~sh
bash ship_app.sh
~~~

该脚本会重新 build、替换 /Applications/WhisperCppCmd.app 并重启。即使 alias 模式下普通重启已经能看到代码变化，也不能省略 ship。

纯文档或非代码改动，例如 AGENTS.md、README 和本目录记忆文档，不需要 ship。

## 纪律

代码改动完成后先 restart 让用户审核；用户确认后再提交；含代码提交完成后立即 ship。每次重启都要等待 10–15 秒，再检查进程和日志。

