#!/usr/bin/env python3
"""
录音浮窗 - 录音期间在屏幕底部显示极简胶囊：红点 + 时长 + 滚动迷你波形

- 无边框、非激活（不抢焦点）、高 window level（盖过全屏应用）
- 外观：NSGlassEffectView 系统液态玻璃 Regular + 内容（红点/时长/波形经
  setContentView_ 放入玻璃层）。2026-09-08 alias 探针复验：正确用
  setContentView_ 嵌内容时，py2app alias 进程同样渲染出真实折射，旧的
  “系统材质不可用”结论已过期作废，旧自绘采样管线已删除。
  白字/白条/红点带黑色软阴影保亮背景可读
- show/hide 带 ease-out 淡入淡出（出场轻微上浮）
- 波形是真实电平历史：每 tick 把 attack/decay 平滑后的电平追加进环形队列，
  新帧在最右、向左滚动，白色单色、旧帧渐隐
- 60Hz 刷新：从 audio_source 读 RMS 算电平 + 累计录音时长

命名约定：以 `_` 结尾的方法 = ObjC selector（tick:/setLevel:/_finishHide: 等，由
NSTimer/performSelector/Cocoa 调用）；其余内部辅助方法用驼峰、不以 `_` 结尾，
避免被 PyObjC 误解析为 selector。
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Callable, NamedTuple, Optional

import AppKit

import objc
from Foundation import NSObject, NSMakeRect, NSMakeSize, NSColor
from Quartz import (
    CAMediaTimingFunction, CABasicAnimation, CGColorCreateGenericRGB,
)


logger = logging.getLogger(__name__)

_WIDTH = 172
_HEIGHT = 40  # 胶囊：圆角等于半高
_MARGIN_BOTTOM = 80  # 离屏幕底部高度，避开 Dock
_TICK_INTERVAL = 1.0 / 60.0  # UI 刷新间隔（60fps）

# 前景可读性：液态玻璃透出背后内容，纯白文字/图标在亮背景下对比不足。给白色前景加
# 黑色软阴影描边（macOS 菜单栏/HUD 同款），亮/暗背景都清晰，且不影响玻璃折射。
_LEGIBILITY_SHADOW_CG = CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0)
_LEGIBILITY_SHADOW_OPACITY = 0.55
_LEGIBILITY_SHADOW_RADIUS = 2.5
_LEGIBILITY_SHADOW_OFFSET = (0.0, -1.0)

# 系统液态玻璃由 NSGlassEffectView 实时合成，无自绘采样、无截屏权限依赖。

# show/hide 动画
_EASE_OUT = CAMediaTimingFunction.functionWithName_("easeOut")
_EASE_IN_OUT = CAMediaTimingFunction.functionWithName_("easeInEaseOut")
_SHOW_DURATION = 0.22
_HIDE_DURATION = 0.18
_HIDE_FINISH_DELAY = 0.20  # 略大于 _HIDE_DURATION，确保 alpha 已落 0 再 orderOut
_SHOW_LIFT = 6.0  # 出场起点相对 home 下移量（动画时上浮）

# 跟随鼠标：浮窗定位在鼠标上方（底部距鼠标光标高度），避免遮挡鼠标所指内容
_FOLLOW_GAP = 16

# 录制红点（呼吸脉冲表示录音中）
_DOT_SIZE = 6.0
_DOT_RED = (0.95, 0.30, 0.30)

# 红点呼吸
_BREATH_KEY = "breath"
_BREATH_DURATION = 1.2
_BREATH_DIM = 0.35

# 迷你波形：13 根 2pt 宽细条（间隔 2pt），展示最近 13 帧电平历史（约 0.65s）
_BAR_COUNT = 13
_BAR_WIDTH = 2.0
_BAR_GAP = 2.0
_WAVE_WIDTH = _BAR_COUNT * _BAR_WIDTH + (_BAR_COUNT - 1) * _BAR_GAP
_BAR_MIN_HEIGHT = 2.0  # 静音时显示为一排小点
_ALPHA_FADE = (0.55, 0.95)  # 最旧帧 -> 最新帧的不透明度渐变

# 电平 -> 柱高映射：dB 对数域 [floor, ceil] 拉满到 [0, 1]。
# 绝对区间：底噪 ≈0.003 RMS(-50dB)，近讲正常说话 0.05~0.15(-26~-16dB)，
# 大声 0.25+(-12dB)。ceil 取 -12dB，保证日常说话落中段、大声顶满。
_LEVEL_DB_FLOOR = -50.0  # rms≈0.003，底噪及以下归零
_LEVEL_DB_CEIL = -12.0  # rms≈0.25，很响即顶满

# 电平平滑时间常数（attack 快上、decay 慢下）
_ATTACK_TAU = 0.10
_DECAY_TAU = 0.30


def rms_to_bar_level(rms: float) -> float:
    """RMS -> 0..1 柱高显示电平（dB 对数域映射）。

    本机实测（2026-08-15 overlay tick 日志）：静音底噪 RMS≈0.003（-50dB）。
    2026-09-06 近讲复测：正常说话 RMS 0.05~0.15，大声 0.25+，故 ceil 取 -12dB。
    正常说话落在中段、大声顶满、底噪归零。末段开方让中低段更饱满。纯函数便于单测。
    """
    if rms <= 0.0:
        return 0.0
    db = 20.0 * math.log10(rms)
    norm = (db - _LEVEL_DB_FLOOR) / (_LEVEL_DB_CEIL - _LEVEL_DB_FLOOR)
    norm = 0.0 if norm < 0.0 else (1.0 if norm > 1.0 else norm)
    return math.sqrt(norm)


def follow_frame(mouse_x, mouse_y, gap, width, height, screens):
    """计算「跟随鼠标」浮窗左下角全局坐标 (left, bottom)。

    纯函数（不碰 Cocoa 类型），便于单测。screens 为可迭代元组序列：
    (fx, fy, fw, fh, vfx, vfy, vfw, vfh)，前 4 个=full frame 找鼠标所在屏，
    后 4 个=visibleFrame 用来夹紧（避开 Dock/菜单栏）。浮窗定位在鼠标上方居中。
    """
    # 选 full frame 包含鼠标点的屏幕，无则取第 0 个
    target = None
    screens = list(screens)
    for s in screens:
        fx, fy, fw, fh = s[0], s[1], s[2], s[3]
        if fx <= mouse_x <= fx + fw and fy <= mouse_y <= fy + fh:
            target = s
            break
    if target is None and screens:
        target = screens[0]
    if target is None:
        # 无任何屏幕信息：以鼠标点为基准不夹紧
        return (mouse_x - width / 2.0, mouse_y + gap)

    vfx, vfy, vfw, vfh = target[4], target[5], target[6], target[7]
    desired_left = mouse_x - width / 2.0
    desired_bottom = mouse_y + gap
    # 仅当浮窗小于可见区才夹紧；否则贴可见区左下角（避免 min>max 反转）
    if width <= vfw:
        left = min(max(desired_left, vfx), vfx + vfw - width)
    else:
        left = vfx
    if height <= vfh:
        bottom = min(max(desired_bottom, vfy), vfy + vfh - height)
    else:
        bottom = vfy
    return (left, bottom)


class _Rect(NamedTuple):
    x: float
    y: float
    w: float
    h: float


class CapsuleLayout(NamedTuple):
    dot: _Rect
    label: _Rect
    wave: _Rect
    status: _Rect


def capsule_layout(width: float, height: float) -> CapsuleLayout:
    """由宽高推导胶囊内点位；纯函数便于单测。"""
    pad_x = 16.0
    dot = _Rect(pad_x, (height - _DOT_SIZE) / 2.0, _DOT_SIZE, _DOT_SIZE)
    label_w, label_h = 44.0, 18.0
    label = _Rect(dot.x + dot.w + 8.0, (height - label_h) / 2.0, label_w, label_h)
    wave_h = height - 18.0
    wave = _Rect(width - pad_x - _WAVE_WIDTH, (height - wave_h) / 2.0, _WAVE_WIDTH, wave_h)
    status = _Rect(12.0, (height - label_h) / 2.0, width - 24.0, label_h)
    return CapsuleLayout(dot=dot, label=label, wave=wave, status=status)


def _makeAttributedString(text: str, attrs: dict) -> AppKit.NSAttributedString:
    """构造富文本（模块级辅助，避开 NSObject 子类方法须为 selector 的 PyObjC 约束）。"""
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)


class _WaveformView(AppKit.NSView):
    """自绘迷你波形：维护最近 N 帧电平历史（环形队列），最新帧在最右、向左滚动。

    历史值是真实电平（每 tick 追加 attack/decay 平滑后的值），不做假随机；
    白色单色、旧帧渐隐，静音时退化为一排 2pt 小点。
    """

    def init(self):
        self = objc.super(_WaveformView, self).init()
        if self is None:
            return None
        self._target = 0.0
        self._smoothed = 0.0
        self._history: "deque[float]" = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)
        self._shadow = AppKit.NSShadow.alloc().init()
        self._shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.5))
        self._shadow.setShadowBlurRadius_(2.0)
        self._shadow.setShadowOffset_(NSMakeSize(0.0, -1.0))
        return self

    def setLevel_(self, level: float):
        """仅更新目标值；实际显示值由 updateSmooth() 每帧插值推进。"""
        self._target = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)

    def updateSmooth(self):
        """每 tick 调一次：按 attack/decay 时间常数指数趋近 target，然后追加进历史。"""
        tau = _ATTACK_TAU if self._smoothed < self._target else _DECAY_TAU
        a = 1.0 - math.exp(-_TICK_INTERVAL / tau)
        self._smoothed += (self._target - self._smoothed) * a
        self._history.append(self._smoothed)  # deque(maxlen) 自动挤掉最旧一帧
        self.setNeedsDisplay_(True)

    def resetSmooth(self):
        self._target = 0.0
        self._smoothed = 0.0
        self._history = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        n = len(self._history)
        # 白条加软阴影：Clear 玻璃下亮背景上白条不糊掉（与 label/dot 同款可读性兜底）
        self._shadow.set()
        for i, level in enumerate(self._history):
            # 历史存的是 rms_to_bar_level 的成品显示值（dB 映射已含感知开方），线性映射到高度
            bar_h = _BAR_MIN_HEIGHT + (h - _BAR_MIN_HEIGHT) * level
            # 旧帧渐隐，制造滚动方向感
            alpha = _ALPHA_FADE[0] + (_ALPHA_FADE[1] - _ALPHA_FADE[0]) * (i / max(1, n - 1))
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
            x = bounds.origin.x + i * (_BAR_WIDTH + _BAR_GAP)
            y = bounds.origin.y + (h - bar_h) / 2.0
            AppKit.NSRectFill(NSMakeRect(x, y, _BAR_WIDTH, bar_h))


class RecordingOverlay(NSObject):
    """录音浮窗控制器（NSObject 以便作 NSTimer / performSelector target）。"""

    def init(self):
        self = objc.super(RecordingOverlay, self).init()
        if self is None:
            return None
        self._level_provider: Callable[[], float] = lambda: 0.0
        self._panel: Optional[AppKit.NSPanel] = None
        self._label: Optional[AppKit.NSTextField] = None
        self._status_label: Optional[AppKit.NSTextField] = None
        self._status_generation: int = 0
        self._mode: str = "recording"  # recording | status
        self._wave: Optional[_WaveformView] = None
        self._dot: Optional[AppKit.NSView] = None
        self._timer: Optional[AppKit.NSTimer] = None
        self._start: float = 0.0
        self._home_frame = NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        self._visible: bool = False
        self._animating_out: bool = False
        self._label_attrs: Optional[dict] = None
        self._follow_mouse: bool = False  # 跟随鼠标（菜单勾选）
        self._build()
        return self

    def setLevelProvider_(self, provider: Callable[[], float]):
        self._level_provider = provider

    def _build(self):
        self._home_frame = self._computeHomeFrame()
        content_rect = self._home_frame

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            content_rect,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        # 系统玻璃自带边缘和阴影，窗口级阴影会叠出厚重灰 halo，保持关闭。
        panel.setHasShadow_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setMovable_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        # 两层（自底向上）：NSGlassEffectView 系统液态玻璃（含真实折射/高光）→ 内容。
        # 2026-09-08 alias 探针复验：NSGlassEffectView + setContentView_ 在
        # py2app alias 进程同样渲染出真实折射，旧结论已过期。内容必须放进普通
        # NSView 再 setContentView_，直接 addSubview_ 会盖住玻璃层。
        glass = AppKit.NSGlassEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        glass.setStyle_(AppKit.NSGlassEffectViewStyleRegular)
        glass.setCornerRadius_(_HEIGHT / 2.0)
        glass.setTintColor_(None)
        glass.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content = AppKit.NSView.alloc().initWithFrame_(glass.bounds())
        content.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        layout = capsule_layout(_WIDTH, _HEIGHT)

        # 录制红点（layer-backed 以承载呼吸动画）
        dot = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(layout.dot.x, layout.dot.y, layout.dot.w, layout.dot.h)
        )
        dot.setWantsLayer_(True)
        dot_layer = dot.layer()
        r, g, b = _DOT_RED
        dot_layer.setBackgroundColor_(CGColorCreateGenericRGB(r, g, b, 1.0))
        dot_layer.setCornerRadius_(_DOT_SIZE / 2.0)
        # 黑色软阴影：亮背景也可读（呼吸动画作用于 layer opacity，阴影随之协调淡入淡出）
        dot_layer.setShadowColor_(_LEGIBILITY_SHADOW_CG)
        dot_layer.setShadowOpacity_(_LEGIBILITY_SHADOW_OPACITY)
        dot_layer.setShadowRadius_(_LEGIBILITY_SHADOW_RADIUS)
        dot_layer.setShadowOffset_(_LEGIBILITY_SHADOW_OFFSET)

        # 时长（白色 + 黑色软阴影，液态玻璃上亮/暗背景都可读）
        label = AppKit.NSTextField.alloc().initWithFrame_(
            NSMakeRect(layout.label.x, layout.label.y, layout.label.w, layout.label.h)
        )
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        self._label = label  # _updateLabelText 依赖
        self._label_attrs = self._buildLabelAttrs()
        self._label.setAttributedStringValue_(_makeAttributedString("00:00", self._label_attrs))

        # 迷你波形（右侧贴边）
        wave = _WaveformView.alloc().init()
        wave.setFrame_(NSMakeRect(layout.wave.x, layout.wave.y,
                                  layout.wave.w, layout.wave.h))

        # 状态文字：与录音态整行内容同宽同中心，红点隐藏后不留空位，视觉居中。
        status_label = AppKit.NSTextField.alloc().initWithFrame_(
            NSMakeRect(layout.status.x, layout.status.y,
                       layout.status.w, layout.status.h)
        )
        status_label.setEditable_(False)
        status_label.setSelectable_(False)
        status_label.setBezeled_(False)
        status_label.setDrawsBackground_(False)
        status_label.setAlignment_(AppKit.NSTextAlignmentCenter)
        status_label.setHidden_(True)

        content.addSubview_(dot)
        content.addSubview_(label)
        content.addSubview_(status_label)
        content.addSubview_(wave)
        glass.setContentView_(content)
        panel.setContentView_(glass)

        self._panel = panel
        self._label = label
        self._status_label = status_label
        self._wave = wave
        self._dot = dot
        logger.info("录音浮窗已构建：%sx%s（液态玻璃胶囊）", _WIDTH, _HEIGHT)

    @objc.python_method
    def _computeHomeFrame(self):
        """按当前主屏重算底部居中 frame（计入 origin，多显示器/切屏后宽度不再过期）。

        mainScreen 无 key window 时返回带菜单栏的主屏（origin 恒 (0,0)）；None 时回退名义居中。
        """
        screen = AppKit.NSScreen.mainScreen()
        if screen is not None:
            f = screen.frame()
            x = f.origin.x + (f.size.width - _WIDTH) / 2.0
            y = f.origin.y + _MARGIN_BOTTOM
        else:
            x = (1440.0 - _WIDTH) / 2.0
            y = _MARGIN_BOTTOM
        return NSMakeRect(x, y, _WIDTH, _HEIGHT)

    @objc.python_method
    def _mouseFollowFrame(self):
        """计算跟随鼠标的 frame：鼠标上方居中，夹紧到鼠标所在屏的可见区。"""
        try:
            loc = AppKit.NSEvent.mouseLocation()
        except Exception:
            return self._home_frame
        screens = []
        for s in AppKit.NSScreen.screens() or []:
            f = s.frame()
            v = s.visibleFrame()
            screens.append((f.origin.x, f.origin.y, f.size.width, f.size.height,
                            v.origin.x, v.origin.y, v.size.width, v.size.height))
        left, bottom = follow_frame(loc.x, loc.y, _FOLLOW_GAP, _WIDTH, _HEIGHT, screens)
        return NSMakeRect(left, bottom, _WIDTH, _HEIGHT)

    def setFollowMouse_(self, enabled):
        """菜单勾选「跟随鼠标」。录音中途切换时立即 snap，避免卡在旧位置。"""
        self._follow_mouse = bool(enabled)
        if self._panel is not None and self._visible and not self._animating_out:
            target = self._mouseFollowFrame() if self._follow_mouse else self._home_frame
            self._panel.setFrame_display_(target, False)

    def _buildLabelAttrs(self) -> dict:
        """时长文字的富文本属性：等宽数字 + 白色 + 黑色软阴影（液态玻璃可读性）。"""
        if hasattr(AppKit.NSFont, "monospacedDigitSystemFontOfSize_weight_"):
            font = AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(12.0, 0.0)
        else:  # pragma: no cover - 老系统兜底
            font = AppKit.NSFont.systemFontOfSize_(12.0)
        shadow = AppKit.NSShadow.alloc().init()
        shadow.setShadowColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, _LEGIBILITY_SHADOW_OPACITY)
        )
        shadow.setShadowBlurRadius_(_LEGIBILITY_SHADOW_RADIUS)
        shadow.setShadowOffset_(
            NSMakeSize(_LEGIBILITY_SHADOW_OFFSET[0], _LEGIBILITY_SHADOW_OFFSET[1])
        )
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(AppKit.NSLeftTextAlignment)
        return {
            AppKit.NSFontAttributeName: font,
            AppKit.NSForegroundColorAttributeName: NSColor.whiteColor(),
            AppKit.NSShadowAttributeName: shadow,
            AppKit.NSParagraphStyleAttributeName: para,
        }

    # ---------------- 显示 / 隐藏（带 ease-out 动画 + 防重入） ----------------

    def _enter_recording_mode(self):
        """切录音态：红点加时长加波形可见，状态文字隐藏。"""
        self._mode = "recording"
        if self._dot is not None:
            self._dot.setHidden_(False)
        if self._label is not None:
            self._label.setHidden_(False)
        if self._wave is not None:
            self._wave.setHidden_(False)
        if self._status_label is not None:
            self._status_label.setHidden_(True)
        self._startBreath()

    def _enter_status_mode(self, text: str):
        """切状态态：隐藏录音元素，居中显示提示语。红点呼吸和波形 tick 一并停掉。"""
        self._mode = "status"
        self._stopBreath()
        self._stopTimer()
        if self._dot is not None:
            self._dot.setHidden_(True)
        if self._label is not None:
            self._label.setHidden_(True)
        if self._wave is not None:
            self._wave.setHidden_(True)
        if self._status_label is not None:
            value = str(text or "")
            if len(value) > 18:
                value = value[:17] + "…"
            self._status_label.setStringValue_(value)
            self._status_label.setHidden_(False)

    @objc.python_method
    def _presentPanel(self):
        """出场动画共用：录音态和状态态走同一条显示路径。"""
        if self._panel is None:
            return
        if self._visible:
            # 已可见（静止或正在淡入）。若正卡在淡出中途，把 alpha 平滑拉回 1。
            if self._panel.alphaValue() < 0.999:
                self._panel.animator().setAlphaValue_(1.0)
            return
        p = self._panel
        p.setAlphaValue_(0.0)
        if self._follow_mouse:
            start_frame = self._mouseFollowFrame()
        else:
            start_frame = AppKit.NSOffsetRect(self._home_frame, 0, -_SHOW_LIFT)
        p.setFrame_display_(start_frame, False)
        p.orderFront_(None)
        AppKit.NSAnimationContext.beginGrouping()
        ctx = AppKit.NSAnimationContext.currentContext()
        ctx.setDuration_(_SHOW_DURATION)
        ctx.setTimingFunction_(_EASE_OUT)
        anim = p.animator()
        anim.setAlphaValue_(1.0)
        if not self._follow_mouse:
            anim.setFrame_display_(self._home_frame, False)
        AppKit.NSAnimationContext.endGrouping()
        self._visible = True

    def show(self):
        if self._panel is None:
            return
        self._enter_recording_mode()
        # 每次显示按当前主屏重算 home（切屏后宽度/原点可能变化，避免浮窗跑偏）
        self._home_frame = self._computeHomeFrame()
        # 取消任何挂起的淡出收尾（show 打断 hide）
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        self._animating_out = False

        self._start = time.monotonic()
        if self._wave is not None:
            self._wave.resetSmooth()
        if self._label is not None:
            self._label.setStringValue_("00:00")
        self._startTimer()
        self._presentPanel()

    def show_status(self, text, timeout=1.0, generation=0):
        """单胶囊状态提示：复用录音 panel 显示提示语，timeout 秒后自动 hide。

        generation 是 controller 的 _capsule_generation 快照：定时到点时若代次
        已变（新一轮录音开始），直接丢弃，杜绝旧结果定时误杀新胶囊。
        timeout=None 表示常驻（转写中）。"""
        if self._panel is None:
            return
        self._status_generation = generation
        self._enter_status_mode(text)
        self._home_frame = self._computeHomeFrame()
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        self._animating_out = False
        self._presentPanel()
        if timeout is not None:
            self.performSelector_withObject_afterDelay_("hideStatus:", generation, timeout)

    def hideStatus_(self, generation):
        # 定时熄灭只在同代次结果态生效：新一轮录音已开始则丢弃，避免旧定时杀新胶囊。
        try:
            generation = int(generation)
        except (TypeError, ValueError):
            generation = -1
        if generation != getattr(self, "_status_generation", 0):
            return
        if self._mode != "status":
            return
        self.hide()

    def hide(self):
        if self._panel is None:
            return
        self._stopBreath()
        self._stopTimer()
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)

        if not self._visible or self._animating_out:
            # 不可见 / 已在淡出：直接兜底收掉，避免动画堆叠
            self._forceHide()
            return

        self._animating_out = True
        p = self._panel
        AppKit.NSAnimationContext.beginGrouping()
        ctx = AppKit.NSAnimationContext.currentContext()
        ctx.setDuration_(_HIDE_DURATION)
        ctx.setTimingFunction_(_EASE_OUT)
        p.animator().setAlphaValue_(0.0)
        AppKit.NSAnimationContext.endGrouping()

        # NSAnimationContext 的 completionHandler 在 PyObjC 下不可靠，改用延时调度
        self.performSelector_withObject_afterDelay_(
            "_finishHide:", None, _HIDE_FINISH_DELAY
        )

    def _finishHide_(self, _sender):
        # 期间被 show 打断（_animating_out 已被清零）则不收尾
        if not self._animating_out:
            return
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.setAlphaValue_(1.0)
        self._animating_out = False
        self._visible = False

    def _forceHide(self):
        """兜底：连按 / 异常时直接隐藏，不留半透明残影。"""
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.setAlphaValue_(1.0)
        self._animating_out = False
        self._visible = False

    # ---------------- 红点呼吸 ----------------

    def _startBreath(self):
        if self._dot is None or self._dot.layer() is None:
            return
        layer = self._dot.layer()
        if layer.animationForKey_(_BREATH_KEY) is not None:
            return  # 已在跑
        breath = CABasicAnimation.animationWithKeyPath_("opacity")
        breath.setDuration_(_BREATH_DURATION)
        breath.setFromValue_(1.0)
        breath.setToValue_(_BREATH_DIM)
        breath.setAutoreverses_(True)
        breath.setRepeatCount_(float("inf"))
        breath.setTimingFunction_(_EASE_IN_OUT)
        layer.addAnimation_forKey_(breath, _BREATH_KEY)

    def _stopBreath(self):
        if self._dot is None or self._dot.layer() is None:
            return
        self._dot.layer().removeAnimationForKey_(_BREATH_KEY)
        self._dot.layer().setOpacity_(1.0)

    # ---------------- 定时刷新 ----------------

    def _startTimer(self):
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _TICK_INTERVAL, self, "tick:", None, True
        )

    def _stopTimer(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def tick_(self, _sender):
        if self._panel is None:
            return
        # 跟随鼠标：每 tick 把浮窗挪到鼠标上方（tick 在主线程，与 show 的 alpha 动画互不干扰）
        if self._follow_mouse and self._visible and not self._animating_out:
            self._panel.setFrame_display_(self._mouseFollowFrame(), False)
        try:
            rms = float(self._level_provider() or 0.0)
        except Exception as e:
            logger.warning("电平读取失败：%r", e)
            rms = 0.0
        if self._wave is not None:
            self._wave.setLevel_(rms_to_bar_level(rms))
            self._wave.updateSmooth()
        if self._label is not None:
            elapsed = max(0.0, time.monotonic() - self._start)
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60
            self._label.setAttributedStringValue_(
                _makeAttributedString(f"{mins:02d}:{secs:02d}", self._label_attrs)
            )
