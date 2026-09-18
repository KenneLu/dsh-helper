# -*- coding: utf-8 -*-
# TEMPLATE-LOCAL-OVERRIDE: dsh 的美术资产是手工 png（resources/img，家族唯一入库
#   资产）——本模块是「资产 → 双 ico 派生器」，不是纯代码画（纯代码画形态见 l-s2t）。
"""图标生成：从 resources/img/dsh-helper-icon.png 派生双 ico（G5 帧表）。

生成两个 .ico：
  dsh-helper.ico          托盘态：16/24/32/48/64/256 帧
  dsh-helper-taskbar.ico  任务栏/窗口/exe：按 Windows 外壳真实索取的像素铺帧，
                          覆盖 100%~200% DPI（标题栏/任务栏/Alt-Tab），避免缩小发糊。

托盘运行时继续直接读 png（行为不变）；ico 供 PyInstaller --icon 与窗口/exe 资源。
"""
import os
from pathlib import Path

from PIL import Image

APP_ID = "dsh-helper"
ASSET = Path(__file__).resolve().parents[1] / "resources" / "img" / f"{APP_ID}-icon.png"

TRAY_SIZES = (16, 24, 32, 48, 64, 256)
# 100%~200% DPI 下外壳真实索取的像素档（reme-helper 同款清单）
TASKBAR_SIZES = (16, 20, 24, 28, 30, 32, 36, 40, 42, 48, 56, 64, 96, 128, 256)


def make_icons(base_dir):
    """从资产 png 派生双 ico 到 base_dir，返回 (托盘, 任务栏) 路径。"""
    tray_path = os.path.join(base_dir, f"{APP_ID}.ico")
    taskbar_path = os.path.join(base_dir, f"{APP_ID}-taskbar.ico")
    img = Image.open(ASSET).convert("RGBA")
    img = img.resize((256, 256), Image.LANCZOS)  # 派生基图统一 256，帧表由 ICO 插件缩放
    img.save(tray_path, sizes=[(s, s) for s in TRAY_SIZES])
    img.save(taskbar_path, sizes=[(s, s) for s in TASKBAR_SIZES])
    return tray_path, taskbar_path


if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（ico 落根，构建按根取）
    t, k = make_icons(base)
    print("OK", t, k, "exists:", os.path.exists(t), os.path.exists(k))
