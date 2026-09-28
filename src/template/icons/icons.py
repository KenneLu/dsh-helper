# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/icons/icons.py | TEMPLATE-VER: 2.1.0
# 2.1.0（W6，Decision 6）：**构建工具化 + 状态帧生成**——新增 make_state_icons：
#   工具经 appconfig 提供 ICON_STATE_ARTISTS（状态绘制器表，key=状态名，callable
#   (base256)->Image256），构建期产 resources/icons/<state>/<size>.png 全套帧；
#   运行时由 tray_icons（T6b）加载，包内零绘制代码。旧 make_icons（exe/taskbar
#   ico）保留不变——exe 图标仍是单形态多帧 ico。
# 2.0.1（W1 改名过渡）：模块互引改双式导入（try modules. / except template.）；W1 收尾步统一。
"""T6｜图标构建工具（G5 + W6 静态化）：构建期生成，仓库里不进二进制图标资源。

图形来源二选一（不变）：
  ICON_DRAW(size)  -> PIL.Image   纯代码画（推荐；参考 l-s2t draw_mic）
  ICON_ASSET       -> 相对仓库根的 png 路径（手工资产派生；参考 dsh，OVERRIDE 申报）

产出两类资产：
  exe/taskbar ico（make_icons，2.0.0 语义不变）：
    <APP_ID>.ico / <APP_ID>-taskbar.ico
  **状态帧目录**（make_state_icons，2.1.0 新）：
    resources/icons/<state>/<size>.png —— tray_icons 运行时按档加载（F25：状态
    角标 resize 会偏移，必须按档存帧）。状态集 = appconfig 的 ICON_STATE_ARTISTS
    键集 + "default"（恒有，恒用 base 原图）。

方案开关（Decision 6）：A 指定现成图标 = ICON_ASSET 路线（资产直派生）；
B 软件生成 = ICON_DRAW + ICON_STATE_ARTISTS 路线。两套并存，当前四工具均 B。

build.bat 接入：GATE `python src\\modules\\icons\\icons.py` -> 状态帧落
resources/icons/ -> PyInstaller `--icon taskbar.ico` + `--add-data resources` ->
交付断言「全套状态资产在位 + ICON_STATES 键集 == 资产目录状态集」。
"""
import os
from pathlib import Path

from PIL import Image

from template.appconfig import APP_ID, ICON_DRAW, ICON_ASSET

try:
    from template.appconfig import ICON_STATE_ARTISTS
except ImportError:          # 旧参数件没有该键：单 default 形态（等价 2.0.x 行为）
    ICON_STATE_ARTISTS = None

TRAY_SIZES = (16, 24, 32, 48, 64, 256)
# 100%~200% DPI 下外壳真实索取的像素档（reme-helper 同款清单）
TASKBAR_SIZES = (16, 20, 24, 28, 30, 32, 36, 40, 42, 48, 56, 64, 96, 128, 256)
# 状态帧运行档（tray_icons.TRAY_FRAMES 同源；256 档只进 ico 不进贴图目录）
STATE_SIZES = (16, 24, 32, 48, 64)


def state_keys():
    """状态全集：ICON_STATE_ARTISTS 键集 ∪ {default}。default 恒在（兜底帧）。"""
    keys = set(ICON_STATE_ARTISTS or {})
    keys.add("default")
    return sorted(keys)


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


def make_state_icons(base_dir):
    """在 base_dir/resources/icons/ 下生成全套状态帧，返回 {state: [files]}。

    default 态 = base 原图；其余态 = ICON_STATE_ARTISTS[state](base256) 的返回
    （绘制器拿到 256 基图，自行叠状态层——角标/变色/灰度）。按 STATE_SIZES 逐档
    从 256 基图 resize（LANCZOS）——离线一次成本，运行时零缩放。
    """
    base = base_image()
    root = Path(base_dir) / "resources" / "icons"
    out = {}
    for state in state_keys():
        art = base if state == "default" else (ICON_STATE_ARTISTS or {}).get(state)
        if art is None:
            raise RuntimeError("no artist for state %r (ICON_STATE_ARTISTS)" % state)
        img = art(base) if callable(art) else art
        files = []
        d = root / state
        d.mkdir(parents=True, exist_ok=True)
        for size in STATE_SIZES:
            f = d / f"{size}.png"
            (img if size == 256 else img.resize((size, size), Image.LANCZOS)).save(f)
            files.append(str(f))
        out[state] = files
    return out


if __name__ == "__main__":
    # dev 态 ico 落仓库根：向上找 main.py 所在的 src/，其父级即根（与模块深度无关）
    _src = next((p for p in Path(__file__).resolve().parents if (p / "main.py").exists()), None)
    base = str(_src.parent) if _src else os.getcwd()
    t, k = make_icons(base)
    states = make_state_icons(base)
    print("OK", t, k, "states:", {s: len(v) for s, v in states.items()},
          "exists:", os.path.exists(t), os.path.exists(k))

