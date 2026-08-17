# 浮窗玻璃材质实验结论

2026-08-15 对录音浮窗做了多轮真实 app 验收，最终定版为：

- _GlassSkin 纯 alpha 半透明底。
- 黑色 0.05 基色，当前 _SCRIM_ALPHA 为 0.35。
- 白色全周描边和顶部亮弧。
- 红点、白字和白色波形带黑色软阴影。
- 背景细节应当透出，内容在亮背景上仍可读。

## 系统材质结论

以下材质不要再仅凭探针进程重新尝试：

1. NSGlassEffectView Regular 会随背景亮度翻转成恒定灰块。
2. NSGlassEffectView Clear 模糊过重且不可控。
3. tintColor 在 34 pt 胶囊高度几乎不产生有效效果。
4. NSVisualEffectView 在裸探针进程里正常，但在本项目的 py2app alias、Accessory policy 和 pynput 常驻进程里退化为不透明实底。

探针进程正常不代表真实 app 正常；材质层修改必须在真实 app 中截图和用户验收。

## 当前实现方向

纯 alpha 是唯一稳定可控的透明方案。当前未提交的后续实现增加了：

- Quartz 截取浮窗下方内容。
- CIGaussianBlur 做轻度自绘背景模糊。
- _GlassSkin 负责半透明 scrim、描边和顶部高光。
- 移动时及时重采样，静止时节流刷新。

若继续微调，优先调整 _SCRIM_ALPHA、_EDGE_ALPHA 和 _EDGE_TOP_ALPHA；不要重新引入系统材质层，除非先在真实 app 进程内完成 A/B 验收。

