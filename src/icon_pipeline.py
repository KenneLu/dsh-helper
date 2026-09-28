# -*- coding: utf-8 -*-
"""dsh 图标像素链（W6/D12：自 main.py 运行时退役挪构建期，跑一次产状态帧）。

视觉语义与旧运行时逐像素等价（_remove_baked_background → 裁剪居中 256 画布 →
running=_brighten_beacon 暖金提亮 / stopped=灰度 colorize）。appconfig 的
ICON_STATE_ARTISTS 经由本件消费；运行时零绘制（tray_icons 只加载）。
"""
from PIL import Image, ImageDraw, ImageOps


def remove_baked_background(image):
    """角点洪水填充去掉资产图烘焙的浅/深纯色背景（旧 _remove_baked_background）。"""
    alpha = image.getchannel("A")
    if alpha.getextrema() == (0, 0):
        return image
    corners = [(0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)]
    for point in corners:
        r, g, b, a = image.getpixel(point)
        neutral = max(r, g, b) - min(r, g, b) <= 22
        light_background = min(r, g, b) >= 180
        dark_background = max(r, g, b) <= 28
        if a and neutral and (light_background or dark_background):
            ImageDraw.floodfill(image, point, (0, 0, 0, 0), thresh=48)
    return image


def centered_base(base):
    """模板 256 基图 → 去背景 → 有效内容裁剪 → 居中到 256 画布（旧 _load_icon_base 语义）。"""
    source = remove_baked_background(base.copy())
    alpha = source.getchannel("A")
    # 边缘残留 alpha=1 的孤立像素忽略后再裁剪，避免透明边缘把有效内容压小
    trim_alpha = alpha.point(lambda value: 255 if value > 8 else 0)
    bbox = trim_alpha.getbbox()
    if bbox:
        source = source.crop(bbox)
    source.thumbnail((240, 240), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    canvas.alpha_composite(source, ((256 - source.width) // 2, (256 - source.height) // 2))
    return canvas


def brighten_beacon(image):
    """运行态轻微提亮，保留图标资源本身的暖金色（旧 _brighten_beacon）。"""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a and r >= 150 and g >= 100 and b <= 135 and r >= b + 80 and g >= b + 45:
                pixels[x, y] = (
                    min(255, int(r * 1.01 + 1)),
                    min(255, int(g * 1.04 + 4)),
                    max(0, int(b * 0.90)),
                    a,
                )
    return image


def state_image(base, running):
    """ICON_STATE_ARTISTS 的绘制器：base(256) → 居中基图 → 状态层（提亮/灰度）。"""
    img = centered_base(base)
    if running:
        return brighten_beacon(img)
    gray = ImageOps.grayscale(img.convert("RGB"))
    gray = ImageOps.colorize(gray, black=(82, 82, 82), white=(205, 205, 205)).convert("RGBA")
    gray.putalpha(img.getchannel("A"))
    return gray
