#!/usr/bin/env python3
"""
录音浮窗 - 录音期间在屏幕底部显示极简胶囊：红点 + 时长 + 滚动迷你波形

- 无边框、非激活（不抢焦点）、高 window level（盖过全屏应用）
- 外观：_BackdropBlurView 提供真实背景采样，_GlassSkin 只负责轻量材质层
  （透明烟灰底 + 高光/内阴影/细描边）+ 内容。
  实测（2026-08-15 逐像素采样）：系统材质层在当前 py2app 常驻进程中不可稳定复用——
  NSGlassEffectView Regular 随背景亮度翻转成恒定灰块、Clear 重度模糊不可控；
  NSVisualEffectView 在探针进程正常、在本 app 进程退化为不透明实底（根因未明）。
  因此用纯 alpha 方案控制透明度，白字/白条/红点带黑色软阴影保亮背景可读
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
import threading
import time
from collections import deque
from typing import Callable, Optional

import AppKit

import objc
import Quartz
from Foundation import NSObject, NSMakeRect, NSMakeSize, NSColor
from PyObjCTools import AppHelper
from Quartz import (
    CAMediaTimingFunction, CABasicAnimation, CGColorCreateGenericRGB, CGRectMake,
)


logger = logging.getLogger(__name__)

_WIDTH = 148
_HEIGHT = 34  # 胶囊：圆角 = 半高
_MARGIN_BOTTOM = 80  # 离屏幕底部高度，避开 Dock
_TICK_INTERVAL = 1.0 / 60.0  # UI 刷新间隔（60fps）

# 前景可读性：液态玻璃透出背后内容，纯白文字/图标在亮背景下对比不足。给白色前景加
# 黑色软阴影描边（macOS 菜单栏/HUD 同款），亮/暗背景都清晰，且不影响玻璃折射。
_LEGIBILITY_SHADOW_CG = CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0)
_LEGIBILITY_SHADOW_OPACITY = 0.55
_LEGIBILITY_SHADOW_RADIUS = 2.5
_LEGIBILITY_SHADOW_OFFSET = (0.0, -1.0)

# _GlassSkin 参数：材质层不再用一块厚重黑色遮罩盖住背景，而是由薄烟灰底、
# 顶部高光和底部内阴影共同塑形。这样在白色背景上不会退化成均匀灰块，
# 在深色/彩色背景上也能保留底下内容的层次。
_SURFACE_ALPHA = 0.20
_SURFACE_WHITE = 0.08
_GLOSS_TOP_ALPHA = 0.14
_GLOSS_MIDDLE_ALPHA = 0.035
_GLOSS_BOTTOM_ALPHA = 0.06
_EDGE_ALPHA = 0.24        # 全周细描边
_EDGE_TOP_ALPHA = 0.42    # 顶部镜面高光
_EDGE_BOTTOM_ALPHA = 0.12  # 底部内阴影

# 自绘背景模糊（系统材质在本 app 进程不可用，见模块 docstring）：
# CGDisplayStream 取 Retina 原始帧 → Lanczos 高质量缩放 → CIGaussianBlur → 胶囊。
# 采样区始终比最终绘制区大，绝不通过低分辨率截图换帧率；需要屏幕录制权限，
# 无权限时其他 app 的窗口内容截不到（只剩壁纸），模糊会失真。
_BACKDROP_LENS_MAGNIFICATION = 1.045  # 轻微放大，模拟放大镜而不拉出拖影
_BACKDROP_BLUR_RADIUS = 1.75          # 点；在 Retina 像素中执行，柔化文字边缘但保留层次
_BACKDROP_CAPTURE_PADDING = 8.0       # 点；为镜片边缘和模糊核预留真实背景
_BACKDROP_POSITION_EPSILON = 0.5  # 点；只忽略亚像素位置抖动
_DISPLAY_STREAM_PIXEL_FORMAT_BGRA = 0x42475241  # kCVPixelFormatType_32BGRA

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
# 数值按本机实测标定：底噪 ≈0.003 RMS(-50dB)，正常说话 0.008~0.02(-42~-34dB)，
# 大声 0.03+(-30dB)。线性映射对 10 倍动态范围的语音不够敏感（正常说话趴底）。
_LEVEL_DB_FLOOR = -50.0  # rms≈0.003，底噪及以下归零
_LEVEL_DB_CEIL = -26.0  # rms≈0.05，很响即顶满

# 电平平滑时间常数（attack 快上、decay 慢下）
_ATTACK_TAU = 0.10
_DECAY_TAU = 0.30


def rms_to_bar_level(rms: float) -> float:
    """RMS -> 0..1 柱高显示电平（dB 对数域映射）。

    本机实测（2026-08-15 overlay tick 日志）：静音底噪 RMS≈0.003（-50dB），
    正常说话 0.008~0.02（-42~-34dB），大声 0.03+。线性映射下正常说话只趴在
    底部（上一版就是这个坑），dB 域把 26dB 的语音动态范围拉满到 [0, 1]，
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


def _makeAttributedString(text: str, attrs: dict) -> AppKit.NSAttributedString:
    """构造富文本（模块级辅助，避开 NSObject 子类方法须为 selector 的 PyObjC 约束）。"""
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)


class _GlassSkin(AppKit.NSView):
    """胶囊材质层：薄烟灰底 + 顶部高光 + 底部内阴影 + 细描边。

    背景模糊由底层 _BackdropBlurView 自绘；所有装饰都裁剪在胶囊路径内，
    避免边缘出现“贴纸”一样的硬矩形。
    """

    def initWithFrame_(self, frame):
        self = objc.super(_GlassSkin, self).initWithFrame_(frame)
        if self is None:
            return None
        # 材质层只在需要时重绘，渐变提前创建避免 drawRect_ 内反复分配。
        self._surface_gradient = AppKit.NSGradient.alloc().initWithColors_([
            NSColor.colorWithCalibratedWhite_alpha_(1.0, _GLOSS_TOP_ALPHA),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, _GLOSS_MIDDLE_ALPHA),
            NSColor.colorWithCalibratedWhite_alpha_(0.0, _GLOSS_BOTTOM_ALPHA),
        ])
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        capsule = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        )

        # 1) 薄烟灰底：相当于轻微的中性密度，不把下方内容压成一块黑灰。
        NSColor.colorWithCalibratedWhite_alpha_(_SURFACE_WHITE, _SURFACE_ALPHA).set()
        capsule.fill()

        # 2) 纵向镜面层：上方略亮、中部透明、底部略暗，给平面模糊增加曲面感。
        context = AppKit.NSGraphicsContext.currentContext()
        context.saveGraphicsState()
        capsule.addClip()
        if self._surface_gradient is not None:
            self._surface_gradient.drawInRect_angle_(bounds, 90.0)
        context.restoreGraphicsState()

        # 3) 全周细描边；内缩半像素避免抗锯齿把边缘画成发白的粗线。
        inset = NSMakeRect(0.7, 0.7, bounds.size.width - 1.4, bounds.size.height - 1.4)
        outline = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            inset, radius - 0.7, radius - 0.7
        )
        outline.setLineWidth_(0.8)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, _EDGE_ALPHA).set()
        outline.stroke()

        # 4) 顶部镜面亮弧：只强调上沿，不把整个轮廓刷成同样亮度。
        context.saveGraphicsState()
        AppKit.NSRectClip(
            NSMakeRect(bounds.origin.x, bounds.size.height * 0.45,
                       bounds.size.width, bounds.size.height * 0.55)
        )
        outline.setLineWidth_(1.15)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, _EDGE_TOP_ALPHA).set()
        outline.stroke()
        context.restoreGraphicsState()

        # 5) 底部内阴影：轻轻压住下沿，替代厚重的系统投影。
        context.saveGraphicsState()
        AppKit.NSRectClip(
            NSMakeRect(bounds.origin.x, bounds.origin.y,
                       bounds.size.width, bounds.size.height * 0.42)
        )
        outline.setLineWidth_(1.0)
        NSColor.colorWithCalibratedWhite_alpha_(0.0, _EDGE_BOTTOM_ALPHA).set()
        outline.stroke()
        context.restoreGraphicsState()


class _BackdropBlurView(AppKit.NSView):
    """自绘背景镜片：截浮窗周围内容（排除浮窗自身）→ 放大/模糊 → 胶囊内绘制。

    系统材质层（NSGlassEffectView/NSVisualEffectView）在本 app 进程实测全部退化，
    故自绘。每次多截一圈边界内容，避免镜片边缘只能看到一条被截断的像素；
    截图和 Core Image 处理放在后台线程，主线程只提交最新位置并绘制已经完成的帧，
    避免同步截图阻塞 60Hz 的界面刷新。待处理请求采用 latest-wins；完成帧还必须
    对应当前窗口位置，移动期间沿用上一帧，避免旧位置的文字被拉成竖条。
    """

    def initWithFrame_(self, frame):
        # 必须重写 initWithFrame_ 初始化字段：NSView 的 initWithFrame: 不会调用
        # 子类的 init，外部统一用 initWithFrame_ 创建，否则后台采样状态会缺失。
        self = objc.super(_BackdropBlurView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._nsimg: Optional[AppKit.NSImage] = None
        self._display_x: Optional[float] = None
        self._display_y: Optional[float] = None
        self._captured_x: Optional[float] = None
        self._captured_y: Optional[float] = None
        self._screen_capture_allowed: Optional[bool] = None

        # updateBackdrop 在主线程每个 60Hz tick 调用；截图和 CI 计算不能放在这里，
        # 否则 CGWindowListCreateImage 偶发抖动会直接拖慢整个 Cocoa run loop。
        self._capture_condition = threading.Condition()
        self._capture_generation = 0
        self._next_request_id = 0
        self._latest_request_id = 0
        self._last_accepted_request_id = 0
        self._pending_request = None
        self._capture_stop = False
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="OverlayBackdrop",
            daemon=True,
        )
        self._capture_requested = False
        self._stream = None
        self._stream_runloop = None
        self._stream_timer = None
        self._stream_image = None
        self._stream_frame_id = 0
        self._stream_last_render_key = None
        self._capture_thread.start()
        return self

    @objc.python_method
    def startCapture(self) -> None:
        """请求启动显示器帧流；实际创建和销毁在后台 run loop 线程完成。"""
        with self._capture_condition:
            self._capture_requested = True
            self._capture_condition.notify()

    @objc.python_method
    def stopCapture(self) -> None:
        """停止显示器帧流，录音浮窗隐藏时释放屏幕采样资源。"""
        with self._capture_condition:
            self._capture_requested = False
            runloop = self._stream_runloop
            self._capture_condition.notify()
        if runloop is not None:
            Quartz.CFRunLoopStop(runloop)

    @objc.python_method
    def invalidateBackdrop(self) -> None:
        """丢弃旧截图，让下一次显示立即采样当前背景。"""
        self._display_x = None
        self._display_y = None
        self._captured_x = None
        self._captured_y = None
        self._nsimg = None
        with self._capture_condition:
            # 正在处理的 Quartz/CI 请求无法强制取消，用 generation 让它完成后
            # 自动丢弃；同时清空尚未开始的旧位置，下一次 tick 会提交新位置。
            self._capture_generation += 1
            self._latest_request_id += 1
            self._pending_request = None
            self._capture_condition.notify()
        self.setNeedsDisplay_(True)

    @objc.python_method
    def updateBackdrop(self, window_number: int, frame) -> None:
        """提交背景采样请求；frame 为浮窗全局 cocoa frame。

        这里只做轻量的坐标计算和请求合并，绝不在主线程同步截图或运行滤镜。
        """
        x, y = frame.origin.x, frame.origin.y
        self._display_x, self._display_y = x, y
        # 即使位置没有变化也持续提交采样：胶囊后面的窗口内容可能在动，
        # 静止位置短路会让玻璃背景冻结在首帧。后台线程仍会合并排队中的旧请求。
        if self._screen_capture_allowed is None:
            # 截屏预检：无屏幕录制权限时其他 app 窗口内容截不到（模糊只剩壁纸）
            try:
                preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
                self._screen_capture_allowed = True if preflight is None else bool(preflight())
                logger.info("背景模糊启动：屏幕录制权限=%s", self._screen_capture_allowed)
            except Exception:
                # 老系统没有预检 API 时仍尝试截图，让实际结果决定是否可用。
                self._screen_capture_allowed = True
        if not self._screen_capture_allowed:
            return

        w, h = frame.size.width, frame.size.height
        padding = _BACKDROP_CAPTURE_PADDING
        capture_w = w + padding * 2.0
        capture_h = h + padding * 2.0
        # Cocoa 使用全局桌面左下原点，Quartz 使用全局桌面左上原点。
        # 翻转基准必须是所有屏幕的最高边界，不能只取主屏高度（副屏可位于主屏上方）。
        screens = AppKit.NSScreen.screens() or []
        desktop_top = None
        for screen in screens:
            screen_frame = screen.frame()
            top = screen_frame.origin.y + screen_frame.size.height
            desktop_top = top if desktop_top is None else max(desktop_top, top)
        if desktop_top is None:
            main_screen = AppKit.NSScreen.mainScreen()
            desktop_top = (
                main_screen.frame().origin.y + main_screen.frame().size.height
                if main_screen is not None else h
            )
        # 扩大截图区域并保留外围 padding；绘制时只显示中间的浮窗尺寸。
        cg = CGRectMake(
            x - padding,
            desktop_top - y - h - padding,
            capture_w,
            capture_h,
        )
        # CGDisplayStream 输出的是当前显示器的本地 Retina 像素，Core Image 原点在
        # 左下角。最终胶囊区域只占原始帧的一部分；镜片源区按放大倍率缩小，之后
        # 用 Lanczos 放回完整胶囊尺寸，保证“放大镜”是连续采样而不是像素块。
        stream_screen = AppKit.NSScreen.mainScreen()
        if stream_screen is not None:
            stream_frame = stream_screen.frame()
            stream_scale = float(stream_screen.backingScaleFactor())
            stream_width = int(round(stream_frame.size.width * stream_scale))
            stream_height = int(round(stream_frame.size.height * stream_scale))
            panel_x = (x - stream_frame.origin.x) * stream_scale
            panel_y = (y - stream_frame.origin.y) * stream_scale
            panel_w = w * stream_scale
            panel_h = h * stream_scale
            source_w = panel_w / _BACKDROP_LENS_MAGNIFICATION
            source_h = panel_h / _BACKDROP_LENS_MAGNIFICATION
            stream_capture = CGRectMake(
                panel_x + (panel_w - source_w) / 2.0 - padding * stream_scale,
                panel_y + (panel_h - source_h) / 2.0 - padding * stream_scale,
                source_w + padding * stream_scale * 2.0,
                source_h + padding * stream_scale * 2.0,
            )
        else:  # pragma: no cover - 无显示器时仅作防御性回退
            stream_scale = 1.0
            stream_width = int(round(capture_w))
            stream_height = int(round(capture_h))
            stream_capture = CGRectMake(0.0, 0.0, capture_w, capture_h)
        with self._capture_condition:
            self._next_request_id += 1
            request_id = self._next_request_id
            self._latest_request_id = request_id
            self._pending_request = {
                "id": request_id,
                "generation": self._capture_generation,
                "window_number": window_number,
                "rect": cg,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "capture_w": capture_w,
                "capture_h": capture_h,
                "padding": padding,
                "stream_display_id": Quartz.CGMainDisplayID(),
                "stream_width": stream_width,
                "stream_height": stream_height,
                "stream_scale": stream_scale,
                "stream_capture_rect": stream_capture,
            }
            self._capture_condition.notify()
        self.startCapture()

    @objc.python_method
    def _capture_loop(self) -> None:
        """用 CGDisplayStream 驱动高刷新背景帧，失败时回退旧截图路径。"""
        try:
            ci = Quartz.CIContext.contextWithOptions_(None)
            scale_filter = Quartz.CIFilter.filterWithName_("CILanczosScaleTransform")
            blur_filter = Quartz.CIFilter.filterWithName_("CIGaussianBlur")
        except Exception:
            logger.exception("后台 CIContext/滤镜创建失败，背景模糊禁用")
            return

        self._stream_ci = ci
        self._stream_scale_filter = scale_filter
        self._stream_blur_filter = blur_filter

        while True:
            with self._capture_condition:
                while not self._capture_requested and not self._capture_stop:
                    self._capture_condition.wait()
                if self._capture_stop:
                    return
                request = self._pending_request
            if request is None:
                continue

            stream = None
            timer = None
            try:
                properties = {
                    Quartz.kCGDisplayStreamQueueDepth: 3,
                    Quartz.kCGDisplayStreamShowCursor: False,
                }
                stream = Quartz.CGDisplayStreamCreate(
                    request["stream_display_id"],
                    request["stream_width"],
                    request["stream_height"],
                    _DISPLAY_STREAM_PIXEL_FORMAT_BGRA,
                    properties,
                    self._display_stream_callback,
                )
                if stream is None:
                    raise RuntimeError("CGDisplayStreamCreate 返回 None")
                runloop = Quartz.CFRunLoopGetCurrent()
                with self._capture_condition:
                    self._stream = stream
                    self._stream_runloop = runloop
                    self._stream_image = None
                    self._stream_frame_id = 0
                    self._stream_last_render_key = None
                source = Quartz.CGDisplayStreamGetRunLoopSource(stream)
                Quartz.CFRunLoopAddSource(
                    runloop, source, Quartz.kCFRunLoopDefaultMode
                )
                timer = Quartz.CFRunLoopTimerCreate(
                    None,
                    Quartz.CFAbsoluteTimeGetCurrent() + _TICK_INTERVAL,
                    _TICK_INTERVAL,
                    0,
                    0,
                    self._render_stream_timer_callback,
                    None,
                )
                Quartz.CFRunLoopAddTimer(
                    runloop, timer, Quartz.kCFRunLoopDefaultMode
                )
                with self._capture_condition:
                    self._stream_timer = timer
                result = Quartz.CGDisplayStreamStart(stream)
                if result != 0:
                    raise RuntimeError(f"CGDisplayStreamStart 返回 {result}")
                Quartz.CFRunLoopRun()
            except Exception:
                logger.exception("CGDisplayStream 启动失败，回退窗口截图路径")
                with self._capture_condition:
                    self._capture_requested = False
                self._capture_loop_legacy()
                return
            finally:
                if timer is not None:
                    try:
                        Quartz.CFRunLoopTimerInvalidate(timer)
                    except Exception:
                        pass
                if stream is not None:
                    try:
                        Quartz.CGDisplayStreamStop(stream)
                    except Exception:
                        pass
                with self._capture_condition:
                    self._stream = None
                    self._stream_runloop = None
                    self._stream_timer = None
                    self._stream_image = None
                    self._stream_last_render_key = None

    @objc.python_method
    def _display_stream_callback(self, status, timestamp, surface, update) -> None:
        """接收显示器源帧；实际镜片输出由同一 run loop 的 60Hz 定时器驱动。"""
        if surface is None:
            return
        if hasattr(Quartz, "kCGDisplayStreamFrameStatusFrameComplete") and status != Quartz.kCGDisplayStreamFrameStatusFrameComplete:
            return
        with self._capture_condition:
            if not self._capture_requested:
                return
            # CIImage 会保留 IOSurface；显示器有新 dirty 帧时只替换源图，
            # 位置变化即使没有新的显示器 dirty 帧，也由独立定时器重新裁剪。
            self._stream_image = Quartz.CIImage.alloc().initWithIOSurface_(surface)
            self._stream_frame_id += 1
        self._render_stream_frame()

    @objc.python_method
    def _render_stream_timer_callback(self, timer, info) -> None:
        """以 60Hz 检查最新位置/源帧，解耦显示器 dirty 回调和浮窗刷新。"""
        self._render_stream_frame()

    @objc.python_method
    def _render_stream_frame(self) -> None:
        """从最新显示器源图渲染一帧，并把 AppKit 更新交回主线程。"""
        with self._capture_condition:
            if (
                not self._capture_requested
                or self._pending_request is None
                or self._stream_image is None
            ):
                return
            request = self._pending_request
            image = self._stream_image
            frame_id = self._stream_frame_id
            render_key = (request["id"], frame_id)
            if render_key == self._stream_last_render_key:
                return
            self._stream_last_render_key = render_key

        try:
            capture_rect = request["stream_capture_rect"]
            cropped = image.imageByCroppingToRect_(capture_rect)
            # 把显示器局部裁剪移到 (0, 0)，让缩放滤镜的几何中心稳定且不依赖屏幕原点。
            local = cropped.imageByApplyingTransform_(
                Quartz.CGAffineTransformMakeTranslation(
                    -capture_rect.origin.x, -capture_rect.origin.y
                )
            )
            rendered = self._renderLensImage(
                local,
                capture_rect.size.width,
                capture_rect.size.height,
                request["w"] * request["stream_scale"],
                request["h"] * request["stream_scale"],
                request["stream_scale"],
                self._stream_ci,
                self._stream_scale_filter,
                self._stream_blur_filter,
            )
            if rendered is None:
                return
            cg_out, output_rect = rendered
            if cg_out is None:
                if not getattr(self, "_warned_ci", False):
                    self._warned_ci = True
                    logger.warning("背景模糊：CI 输出为空")
                return
            if not getattr(self, "_logged_capture", False):
                self._logged_capture = True
                logger.info(
                    "背景模糊首帧完成：stream=%dx%d inner=%dx%d blur=%dx%d",
                    request["stream_width"], request["stream_height"],
                    int(output_rect.size.width), int(output_rect.size.height),
                    Quartz.CGImageGetWidth(cg_out), Quartz.CGImageGetHeight(cg_out),
                )
            AppHelper.callAfter(
                self._acceptBackdropImage,
                request["id"],
                request["generation"],
                request["x"],
                request["y"],
                request["w"],
                request["h"],
                cg_out,
            )
        except Exception:
            with self._capture_condition:
                if self._stream_last_render_key == render_key:
                    self._stream_last_render_key = None
            logger.exception("背景模糊显示器帧处理失败")

    @objc.python_method
    def _capture_loop_legacy(self) -> None:
        """CGDisplayStream 不可用时的窗口截图回退路径。"""
        try:
            ci = Quartz.CIContext.contextWithOptions_(None)
            scale_filter = Quartz.CIFilter.filterWithName_("CILanczosScaleTransform")
            blur_filter = Quartz.CIFilter.filterWithName_("CIGaussianBlur")
        except Exception:
            logger.exception("后台 CIContext/滤镜创建失败，背景模糊禁用")
            return

        while True:
            with self._capture_condition:
                while self._pending_request is None and not self._capture_stop:
                    self._capture_condition.wait()
                if self._capture_stop:
                    return
                request = self._pending_request
                self._pending_request = None

            try:
                src = Quartz.CGWindowListCreateImage(
                    request["rect"],
                    Quartz.kCGWindowListOptionOnScreenBelowWindow,
                    request["window_number"],
                    Quartz.kCGWindowImageBoundsIgnoreFraming,
                )
                if src is None:
                    if not getattr(self, "_warned_none", False):
                        self._warned_none = True
                        logger.warning("背景模糊：截屏返回 None（rect 可能在显示器外）")
                    continue
                pw, ph = Quartz.CGImageGetWidth(src), Quartz.CGImageGetHeight(src)
                if pw <= 0 or ph <= 0:
                    continue
                ci_img = Quartz.CIImage.imageWithCGImage_(src)
                scale_x = pw / request["capture_w"]
                scale_y = ph / request["capture_h"]
                rendered = self._renderLensImage(
                    ci_img,
                    pw,
                    ph,
                    request["w"] * scale_x,
                    request["h"] * scale_y,
                    (scale_x + scale_y) / 2.0,
                    ci,
                    scale_filter,
                    blur_filter,
                )
                if rendered is None:
                    continue
                cg_out, inner = rendered
                if cg_out is None:
                    if not getattr(self, "_warned_ci", False):
                        self._warned_ci = True
                        logger.warning("背景模糊：CI 输出为空")
                    continue
                if not getattr(self, "_logged_capture", False):
                    self._logged_capture = True
                    logger.info(
                        "背景模糊首帧完成：capture=%dx%d inner=%dx%d blur=%dx%d",
                        pw, ph, int(inner.size.width), int(inner.size.height),
                        Quartz.CGImageGetWidth(cg_out), Quartz.CGImageGetHeight(cg_out),
                    )
                AppHelper.callAfter(
                    self._acceptBackdropImage,
                    request["id"],
                    request["generation"],
                    request["x"],
                    request["y"],
                    request["w"],
                    request["h"],
                    cg_out,
                )
            except Exception:
                logger.exception("背景模糊后台采样失败")

    @objc.python_method
    def _renderLensImage(
        self,
        image,
        source_width: float,
        source_height: float,
        panel_width: float,
        panel_height: float,
        scale: float,
        ci,
        scale_filter,
        blur_filter,
    ):
        """把 Retina 源区渲染成稳定的轻微放大镜帧。

        先用 Lanczos 在原始像素上放大，再做小半径高斯模糊，最后只取居中的
        胶囊区域。源区和输出区都保持完整像素密度，因此不会出现“黑字变纯黑、
        白底变纯白”的低分辨率二值化观感；固定中心缩放也不会把移动中的文字
        通过位移补偿拉成竖条。
        """
        if source_width <= 0.0 or source_height <= 0.0:
            return None
        scale_filter.setValue_forKey_(image, "inputImage")
        scale_filter.setValue_forKey_(_BACKDROP_LENS_MAGNIFICATION, "inputScale")
        scale_filter.setValue_forKey_(1.0, "inputAspectRatio")
        scaled = scale_filter.valueForKey_("outputImage")
        if scaled is None:
            return None

        # Lanczos 以 (0, 0) 为缩放原点；下面的输出矩形把它重新对准源区中心，
        # 得到真正居中的镜片，不会向右上角漂移。
        output_rect = CGRectMake(
            source_width * _BACKDROP_LENS_MAGNIFICATION / 2.0 - panel_width / 2.0,
            source_height * _BACKDROP_LENS_MAGNIFICATION / 2.0 - panel_height / 2.0,
            panel_width,
            panel_height,
        )
        # 源区已经额外采样了 _BACKDROP_CAPTURE_PADDING，输出矩形距离边缘还留有
        # 足够的模糊核余量，因此不需要 CIAffineClamp。后者会把有限图像扩成
        # infinite extent；在当前 macOS/Core Image 路径中再接 GaussianBlur 后，
        # createCGImage(from:) 会把内容错误地压到输出左下角，表现为背景“卡死”
        # 或所有文字只剩胶囊左下角的一小块。
        blur_filter.setValue_forKey_(scaled, "inputImage")
        blur_filter.setValue_forKey_(_BACKDROP_BLUR_RADIUS * scale, "inputRadius")
        blurred = blur_filter.valueForKey_("outputImage")
        if blurred is None:
            return None
        cg_out = ci.createCGImage_fromRect_(blurred, output_rect)
        if cg_out is None:
            return None
        return cg_out, output_rect

    @objc.python_method
    def _acceptBackdropImage(
        self,
        request_id: int,
        generation: int,
        x: float,
        y: float,
        w: float,
        h: float,
        cg_out,
    ) -> None:
        """在主线程接收完成帧；位置过时或 request 倒序的帧都不绘制。"""
        with self._capture_condition:
            if generation != self._capture_generation:
                return
            if (
                self._display_x is not None
                and (
                    abs(x - self._display_x) > _BACKDROP_POSITION_EPSILON
                    or abs(y - self._display_y) > _BACKDROP_POSITION_EPSILON
                )
            ):
                return
            # 单个后台 worker 按顺序完成请求，但主线程回调理论上可能延迟或乱序；
            # 只允许 request id 单调前进，避免回调乱序时旧截图覆盖新背景。
            if request_id < self._last_accepted_request_id:
                return
            self._last_accepted_request_id = request_id
        # NSImage/AppKit 只在主线程创建和更新，避免和 Cocoa 绘制并发。
        self._nsimg = AppKit.NSImage.alloc().initWithCGImage_size_(
            cg_out, NSMakeSize(w, h)
        )
        self._captured_x, self._captured_y = x, y
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if self._nsimg is None:
            return
        b = self.bounds()
        radius = b.size.height / 2.0
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            b, radius, radius
        ).addClip()
        context = AppKit.NSGraphicsContext.currentContext()
        context.setImageInterpolation_(AppKit.NSImageInterpolationHigh)
        # 只以胶囊 bounds 绘制同尺寸截图，倍率恒为 1；不把旧帧做位移补偿，
        # 避免移动期间将文字拖成竖向条纹。
        self._nsimg.drawInRect_(b)


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
        self._wave: Optional[_WaveformView] = None
        self._dot: Optional[AppKit.NSView] = None
        self._timer: Optional[AppKit.NSTimer] = None
        self._start: float = 0.0
        self._home_frame = NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        self._visible: bool = False
        self._animating_out: bool = False
        self._label_attrs: Optional[dict] = None
        self._backdrop: Optional[_BackdropBlurView] = None
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
        if hasattr(panel, "setSharingType_") and hasattr(AppKit, "NSWindowSharingNone"):
            # 显示器帧流只应采样浮窗下面的桌面；把浮窗自身采进去会形成
            # 递归的模糊自反馈，表现为拖影和背景逐渐“糊死”。
            panel.setSharingType_(AppKit.NSWindowSharingNone)
        # 系统默认窗口阴影在 34pt 胶囊上会形成厚重灰 halo；材质层自己画细边缘和
        # 内阴影，保留轻盈的悬浮感，同时避免“普通按钮贴纸”观感。
        panel.setHasShadow_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setMovable_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        # 三层（自底向上）：_BackdropBlurView（自绘背景模糊）→ _GlassSkin（半透明底+描边）→ 内容。
        # 注意：勿用 NSVisualEffectView/NSGlassEffectView 材质层——探针进程里渲染正常，
        # 但在本 app 进程里都会退化成不透明实底（NSVisualEffectView）或恒定灰块
        # （NSGlassEffectView 亮度翻转），用户实测多轮确认。
        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        backdrop = _BackdropBlurView.alloc().initWithFrame_(container.bounds())
        backdrop.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        container.addSubview_(backdrop)
        self._backdrop = backdrop
        skin = _GlassSkin.alloc().initWithFrame_(container.bounds())
        skin.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        container.addSubview_(skin)
        content = AppKit.NSView.alloc().initWithFrame_(container.bounds())
        content.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        # 录制红点（layer-backed 以承载呼吸动画）
        dot = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(14, (_HEIGHT - _DOT_SIZE) / 2.0, _DOT_SIZE, _DOT_SIZE)
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
            NSMakeRect(28, (_HEIGHT - 16) / 2.0, 42, 16)
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
        wave.setFrame_(NSMakeRect(_WIDTH - 14 - _WAVE_WIDTH, 8, _WAVE_WIDTH, _HEIGHT - 16))

        content.addSubview_(dot)
        content.addSubview_(label)
        content.addSubview_(wave)
        container.addSubview_(content)
        panel.setContentView_(container)

        self._panel = panel
        self._label = label
        self._wave = wave
        self._dot = dot
        logger.info("录音浮窗已构建：%sx%s（半透明胶囊）", _WIDTH, _HEIGHT)

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

    def show(self):
        if self._panel is None:
            return
        # 每次显示按当前主屏重算 home（切屏后宽度/原点可能变化，避免浮窗跑偏）
        self._home_frame = self._computeHomeFrame()
        if self._backdrop is not None:
            self._backdrop.invalidateBackdrop()
        # 取消任何挂起的淡出收尾（show 打断 hide）
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        self._animating_out = False

        self._start = time.monotonic()
        if self._wave is not None:
            self._wave.resetSmooth()
        if self._label is not None:
            self._label.setStringValue_("00:00")
        self._startTimer()
        self._startBreath()

        if self._visible:
            # 已可见（静止或正在淡入）。若正卡在淡出中途，把 alpha 平滑拉回 1。
            if self._panel.alphaValue() < 0.999:
                self._panel.animator().setAlphaValue_(1.0)
            return

        p = self._panel
        p.setAlphaValue_(0.0)
        # 起始帧：跟随鼠标=直接定位（tick 接管追踪）；否则从 home 下移 lift 出场
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
        self._updateBackdrop()  # 首帧立即采样，避免淡入动画先闪一帧无模糊

    def hide(self):
        if self._panel is None:
            return
        if self._backdrop is not None:
            self._backdrop.stopCapture()
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

    @objc.python_method
    def _updateBackdrop(self):
        """提交背景模糊采样（主线程只提交位置，实际采样在后台完成）。"""
        if self._backdrop is None or self._panel is None:
            return
        if not self._visible or self._animating_out:
            return
        try:
            self._backdrop.updateBackdrop(self._panel.windowNumber(), self._panel.frame())
        except Exception:
            logger.exception("背景模糊重采样失败")

    def tick_(self, _sender):
        if self._panel is None:
            return
        # 跟随鼠标：每 tick 把浮窗挪到鼠标上方（tick 在主线程，与 show 的 alpha 动画互不干扰）
        if self._follow_mouse and self._visible and not self._animating_out:
            self._panel.setFrame_display_(self._mouseFollowFrame(), False)
        self._updateBackdrop()
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
