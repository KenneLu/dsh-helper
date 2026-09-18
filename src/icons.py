# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/icons/icons.py | TEMPLATE-VER: 2.0.0
# TEMPLATE-LOCAL-OVERRIDE: 图形来源 = ICON_ASSET（家族唯一手工 png 资产，非代码画）
"""T6｜代码生成双 ico（G5）：构建期生成，仓库里不进二进制图标资源。

2.0.0：生成**流程**全部在本模板（帧表/写入/__main__ 锚定），工具只通过
appconfig 提供图形来源，二选一：
  ICON_DRAW(size)  -> PIL.Image   纯代码画（推荐，零美术素材；参考 l-s2t draw_mic）
  ICON_ASSET       -> 相对仓库根的 png 路径（手工资产派生；参考 dsh，OVERRIDE 申报）

生成两个 .ico：
  <APP_ID>.ico          托盘态：16/24/32/48/64/256 帧
  <APP_ID>-taskbar.ico  任务栏/窗口/exe：按 Windows 外壳真实索取的像素铺帧，
                        覆盖 100%~200% DPI（标题栏/任务栏/Alt-Tab），缺档缩放发糊。

build.bat 接入四件套：GATE `python src\\icons.py` -> PyInstaller `--icon
<APP_ID>-taskbar.ico` -> 两个 ico `--add-data` 随包 -> 交付断言 taskbar ico 在位。
"""
import os
from pathlib import Path

from PIL import Image

from modules.appconfig import APP_ID, ICON_DRAW, ICON_ASSET

TRAY_SIZES = (16, 24, 32, 48, 64, 256)
# 100%~200% DPI 下外壳真实索取的像素档（reme-helper 同款清单）
TASKBAR_SIZES = (16, 20, 24, 28, 30, 32, 36, 40, 42, 48, 56, 64, 96, 128, 256)


def base_image():
    """图形来源二选一：ICON_ASSET 派生（统一 256 基图）优先，否则 ICON_DRAW(256)。"""
    if ICON_ASSET:
        asset = Path(ICON_ASSET)
        if not asset.is_absolute():
            # 相对路径按仓库根语义：向上找 main.py 所在的 src/，其父级即仓库根
            # ——与本模块所在深度无关（src/icons.py 与 src/modules/icons/ 都命中）
            _root = next((p for p in Path(__file__).resolve().parents
                          if (p / "main.py").exists()),
                         Path(__file__).resolve().parents[1]).parent
            asset = _root / asset
        img = Image.open(asset).convert("RGBA")
        return img.resize((256, 256), Image.LANCZOS)
    if ICON_DRAW:
        return ICON_DRAW(256)
    raise RuntimeError("appconfig must provide ICON_DRAW or ICON_ASSET (G5)")


def make_icons(base_dir):
    """在 base_dir 下生成托盘态与任务栏态两个 ico，返回 (托盘, 任务栏) 路径。"""
    tray_path = os.path.join(base_dir, f"{APP_ID}.ico")
    taskbar_path = os.path.join(base_dir, f"{APP_ID}-taskbar.ico")
    img = base_image()
    img.save(tray_path, sizes=[(s, s) for s in TRAY_SIZES])
    img.save(taskbar_path, sizes=[(s, s) for s in TASKBAR_SIZES])
    return tray_path, taskbar_path


if __name__ == "__main__":
    # dev 态 ico 落仓库根：向上找 main.py 所在的 src/，其父级即根（与模块深度无关）
    _src = next((p for p in Path(__file__).resolve().parents if (p / "main.py").exists()), None)
    base = str(_src.parent) if _src else os.getcwd()
    t, k = make_icons(base)
    print("OK", t, k, "exists:", os.path.exists(t), os.path.exists(k))
