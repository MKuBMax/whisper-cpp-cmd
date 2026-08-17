"""浮窗跟随鼠标的定位夹紧数学（follow_frame）单测。

纯函数，不碰 Cocoa，与 test_overlay.py 一致：GUI 外观/鼠标移动由用户实测。
screens 元组约定：(fx, fy, fw, fh, vfx, vfy, vfw, vfh) —— full frame 找鼠标所在屏，
visibleFrame 用来夹紧（避开 Dock/菜单栏）。
"""

from ui.overlay_window import follow_frame

# 与浮窗常量对齐（_WIDTH/_HEIGHT/_FOLLOW_GAP）
W, H, GAP = 150, 58, 16


def test_centered_above_cursor_single_screen():
    # 鼠标在屏中央：浮窗水平居中、底部在鼠标上方 GAP
    screens = [(0, 0, 1440, 900, 0, 0, 1440, 830)]
    left, bottom = follow_frame(720, 450, GAP, W, H, screens)
    assert left == 720 - W / 2.0
    assert bottom == 450 + GAP


def test_clamp_right_edge():
    screens = [(0, 0, 1440, 900, 0, 0, 1440, 830)]
    left, _ = follow_frame(1420, 450, GAP, W, H, screens)
    assert left == 1440 - W  # 1290，右边界


def test_clamp_left_edge():
    screens = [(0, 0, 1440, 900, 0, 0, 1440, 830)]
    left, _ = follow_frame(20, 450, GAP, W, H, screens)
    assert left == 0  # 左边界


def test_clamp_top_edge():
    # 鼠标贴近菜单栏/顶部：底部夹到可见区顶部
    screens = [(0, 0, 1440, 900, 0, 0, 1440, 830)]
    _, bottom = follow_frame(720, 820, GAP, W, H, screens)
    assert bottom == 830 - H  # 772


def test_negative_origin_secondary_display():
    # 多显示器：副屏在主屏左侧，origin 为负。鼠标在副屏上应选副屏、夹紧到副屏坐标
    screens = [
        (0, 0, 1920, 1080, 0, 0, 1920, 1050),
        (-1920, 0, 1920, 1080, -1920, 0, 1920, 1050),
    ]
    left, bottom = follow_frame(-960, 540, GAP, W, H, screens)
    assert left == -960 - W / 2.0  # 副屏坐标系下居中
    assert bottom == 540 + GAP


def test_overlay_wider_than_visible_frame():
    # overlay 比可见区还宽：min>max 反转，应贴可见区左边（不出现 min>max）
    left, _ = follow_frame(720, 400, GAP, 2000, H, [(0, 0, 1440, 900, 0, 0, 1440, 830)])
    assert left == 0


def test_overlay_taller_than_visible_frame():
    _, bottom = follow_frame(720, 400, GAP, W, 1000, [(0, 0, 1440, 900, 0, 0, 1440, 830)])
    assert bottom == 0  # 贴可见区底部


def test_no_containing_screen_falls_back_to_first():
    # 鼠标不在任何屏内：回退到第 0 个屏夹紧
    left, _ = follow_frame(500, 500, GAP, W, H, [(0, 0, 100, 100, 0, 0, 100, 100)])
    assert left == 0  # 回退屏太小，宽度夹紧后贴左


def test_empty_screens_unclamped():
    # 无屏幕信息：以鼠标点为基准不夹紧（永不抛异常）
    left, bottom = follow_frame(500, 500, GAP, W, H, [])
    assert left == 500 - W / 2.0
    assert bottom == 500 + GAP
