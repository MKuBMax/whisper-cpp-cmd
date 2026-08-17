#!/usr/bin/env python3
"""生成菜单栏图标"""

from PIL import Image, ImageDraw
import os

ICON_SIZE = 22
ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')

def create_icon(filename, colors, draw_func):
    """创建图标"""
    img = Image.new('RGBA', (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_func(draw, img)
    img.save(os.path.join(ICONS_DIR, filename), 'PNG')
    print(f"✓ 生成：{filename}")

def draw_micidle(draw, img):
    """待机麦克风 - 灰色"""
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    # 麦克风主体
    draw.ellipse([cx-6, cy-8, cx+6, cy+6], fill=(150, 150, 150, 255))
    # 麦克风杆
    draw.rectangle([cx-2, cy+6, cx+2, cy+10], fill=(150, 150, 150, 255))
    # 底座
    draw.ellipse([cx-5, cy+9, cx+5, cy+13], fill=(150, 150, 150, 255))

def draw_micrecording(draw, img):
    """录音中 - 红色"""
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    # 麦克风主体
    draw.ellipse([cx-6, cy-8, cx+6, cy+6], fill=(255, 80, 80, 255))
    # 麦克风杆
    draw.rectangle([cx-2, cy+6, cx+2, cy+10], fill=(255, 80, 80, 255))
    # 底座
    draw.ellipse([cx-5, cy+9, cx+5, cy+13], fill=(255, 80, 80, 255))
    # 声波圈
    draw.ellipse([cx-9, cy-11, cx+9, cy+9], outline=(255, 100, 100, 150), width=1)
    draw.ellipse([cx-11, cy-13, cx+11, cy+11], outline=(255, 100, 100, 100), width=1)

def draw_micprocessing(draw, img):
    """处理中 - 蓝色"""
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    # 麦克风主体
    draw.ellipse([cx-6, cy-8, cx+6, cy+6], fill=(80, 150, 255, 255))
    # 麦克风杆
    draw.rectangle([cx-2, cy+6, cx+2, cy+10], fill=(80, 150, 255, 255))
    # 底座
    draw.ellipse([cx-5, cy+9, cx+5, cy+13], fill=(80, 150, 255, 255))
    # 旋转的加载点
    for i in range(3):
        angle = i * (3.14159 * 2 / 3)
        px = cx + int(10 * (angle % 1))
        py = cy - 12
        draw.ellipse([px-2, py-2, px+2, py+2], fill=(80, 150, 255, 200))

def draw_micsuccess(draw, img):
    """成功 - 绿色带勾"""
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    # 麦克风主体
    draw.ellipse([cx-6, cy-8, cx+6, cy+6], fill=(80, 200, 120, 255))
    # 麦克风杆
    draw.rectangle([cx-2, cy+6, cx+2, cy+10], fill=(80, 200, 120, 255))
    # 底座
    draw.ellipse([cx-5, cy+9, cx+5, cy+13], fill=(80, 200, 120, 255))
    # 勾号
    draw.line([cx-4, cy, cx-1, cy+3], fill=(255, 255, 255, 255), width=2)
    draw.line([cx-1, cy+3, cx+4, cy-3], fill=(255, 255, 255, 255), width=2)

def draw_micerror(draw, img):
    """错误 - 橙色带叉"""
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    # 麦克风主体
    draw.ellipse([cx-6, cy-8, cx+6, cy+6], fill=(255, 180, 80, 255))
    # 麦克风杆
    draw.rectangle([cx-2, cy+6, cx+2, cy+10], fill=(255, 180, 80, 255))
    # 底座
    draw.ellipse([cx-5, cy+9, cx+5, cy+13], fill=(255, 180, 80, 255))
    # 叉号
    draw.line([cx-4, cy-3, cx+4, cy+3], fill=(255, 255, 255, 255), width=2)
    draw.line([cx+4, cy-3, cx-4, cy+3], fill=(255, 255, 255, 255), width=2)

if __name__ == '__main__':
    os.makedirs(ICONS_DIR, exist_ok=True)
    
    create_icon('mic_idle.png', 'gray', draw_micidle)
    create_icon('mic_recording.png', 'red', draw_micrecording)
    create_icon('mic_processing.png', 'blue', draw_micprocessing)
    create_icon('mic_success.png', 'green', draw_micsuccess)
    create_icon('mic_error.png', 'orange', draw_micerror)
    
    print(f"\n✓ 图标已生成到：{ICONS_DIR}")
