# WhisperCppCmd 项目记忆

这是从 Claude Code 项目 memory 迁移到仓库的长期项目背景，最后整理于 2026-08-15。

## 怎么使用

- [AGENTS.md](../../AGENTS.md) 保存每次开发都必须遵守的短规则。
- 本目录保存决策背景、实测数据和维护注意事项；涉及某个领域时读取对应专题，不需要每次展开所有历史。
- 代码、测试和用户最新确认优先于记忆文档。发现记忆过期时，先以当前实现为准，再更新文档。
- 不把完整 Claude 或 Codex 会话历史复制进仓库；这里只保留可复用的项目知识。

## 项目概览

- [项目概览与架构](project-overview.md)
- [Python 环境](project-python-env.md)
- [路线图与明确不做项](project-roadmap.md)
- [开发与部署流程](project-dev-workflow.md)
- [重启后的验证](restart-verify-process.md)

开发/发布模式边界以 [开发与部署流程](project-dev-workflow.md) 为准：默认只运行 `run_dev.sh`；只有用户明确授权发布时才运行 `ship_app.sh`。

## 已验证的工程决策

- [剪贴板：NSPasteboard 优先](clipboard-nspasteboard-preference.md)
- [PyObjC GUI 的 signal pump](pyobjc-signal-pump.md)
- [音频卡死根因：僵尸 AudioUnit](hang-root-cause-zombie-audiounit.md)
- [音频自愈策略对比](audio-strategy-github-comparison.md)
- [音频自愈系统维护禁区](audio-self-healing-maintenance.md)
- [扬声器音乐串扰与 ducking](music-bleed-tier0-ducking.md)
- [GUI 子进程 UTF-8 坑](gui-subprocess-ascii-decode-trap.md)
- [浮窗玻璃材质实验结论](overlay-glass-experiments.md)

## 产品方向

- [产品灵感与路线修正](product-inspiration-roadmap.md)


## 当前产品收敛

- [2026-09-05 简约体验审计与重构](simple-experience-audit.md)：最新用户目标、已确认缺陷、修复范围与验收边界；优先于旧欢迎页、仪表盘和常驻胶囊方案。
- [简约体验重构后续待办](../REFACTOR_TODO.md)：当前交接状态、未完成的真机验收、界面打磨与交付收尾。
