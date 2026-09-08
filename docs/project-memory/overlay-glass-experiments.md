# 浮窗玻璃材质实验结论

2026-09-08 推翻 2026-08-15 结论：录音浮窗改用 `NSGlassEffectView`
Regular 系统液态玻璃，用户在测试中亲眼确认效果不错。

## 关键发现

旧结论说系统材质在真实 app 进程不可用，这个判断错了。真正的原因是
旧代码把内容用 `addSubview_` 直接盖在玻璃层上，玻璃被遮住，只剩灰块。
正确用法是内容先放进普通 `NSView`，再 `setContentView_` 嵌进玻璃层，
玻璃折射和高光正常出现。2026-09-08 alias 探针和裸进程探针都验证通过。

## 当前实现

- `NSGlassEffectView` Regular，圆角等于半高，无 tint。
- 内容经 `setContentView_` 放入玻璃层，禁止 `addSubview_` 直盖。
- 删除 `_BackdropBlurView` 自绘采样管线和 `_GlassSkin`，不再需要截屏权限、
  后台采样线程和限频门控。
- 白字、波形、红点保留黑色软阴影，保亮背景可读。

## 维护注意

- 动玻璃层结构前先看 `ui/overlay_window.py` 的 `_build` 注释。
- 外观改动仍需在 DEV 真机验收，探针通过不算数。
