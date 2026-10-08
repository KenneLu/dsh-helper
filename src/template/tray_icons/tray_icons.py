# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/tray_icons/tray_icons.py | TEMPLATE-VER: 1.0.0
# 1.0.0：运行时托盘贴图加载器——**包内零绘制代码**。
#   资产由构建期 icons.make_state_icons 产出（resources/icons/<state>/<size>.png）；
#   本件只做：目录解析（config icon_dir > APP_DIR/resources/icons > 打包内置）、
#   (state,size) 双键缓存、按 DPI 档选帧（核心——resize 状态角标会偏移，必须选帧）、
#   set_state 去重换图（挂 MenuSignature 同一拍语义：菜单开着切状态不闪菜单）、
#   缺图回退 default 且永不崩（回退只 log 一次，不刷屏）。
"""tray_icons｜托盘状态贴图运行时加载。

与 icons.py（构建工具）的分工：那边**生成**全套状态帧，这边**加载**。
pystray 的 icon 后端把 PIL 图存单帧——直接喂大帧在缩放档上会糊，所以
资产按档存帧、运行时按 DPI 选最近档（reme 实测教训）。

装配（消费方 main.py）：
    import tray_icons
    tray_icons.init(config_icon_dir=CFG.get("icon_dir"))   # 启动一次
    icon = pystray.Icon(...); tray_icons.bind(icon, "default")
    # 状态变化处：
    tray_icons.set_state("running")
"""
import os
import sys
from pathlib import Path

from PIL import Image

# 运行时按档选帧的档表（与 icons.TRAY_SIZES 的运行子集一致；256 档不需要——
# pystray 托盘渲染最大 64 一级，256 只进 exe/taskbar ico）
TRAY_FRAMES = (16, 24, 32, 48, 64)

_state = {"icon": None, "current": None, "dirs": [], "missed": set(), "cache": {}}

def _bundled_dirs():
    """打包内置（_MEIPASS）与源码态（仓库根）两类默认落点，存在才收。"""
    dirs = []
    meipass = getattr(sys, "_MEIPASS", "")
    for cand in (Path(meipass) / "resources" / "icons" if meipass else None,
                 Path(__file__).resolve().parents[3] / "resources" / "icons"):
        if cand and cand.is_dir():
            dirs.append(cand)
    return dirs

def init(config_icon_dir=None):
    """解析资产目录优先级（高→低）：config `icon_dir` > APP_DIR/resources/icons > 打包内置。

    同名覆盖语义：逐目录都收，`get` 从优先级高往低找第一张命中。
    """
    dirs = []
    if config_icon_dir:
        p = Path(config_icon_dir)
        if p.is_dir():
            dirs.append(p)
    app_dir = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).resolve().parents[3])
    # 源码态与打包态共用 src 布局下仓库根的 resources/；打包态 _MEIPASS 下同构
    for p in (app_dir / "resources" / "icons",):
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    dirs.extend(d for d in _bundled_dirs() if d not in dirs)
    _state["dirs"] = dirs
    _state["cache"].clear()
    _state["missed"].clear()
    return dirs

def pick_size(scale_hint=None):
    """按 DPI 缩放选帧（reme 形态：max(16, 32*scale/96) 取档表最近档）。"""
    try:
        import ctypes
        scale = scale_hint or ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        scale = scale_hint or 1.0
    want = max(16, int(32 * scale))
    return min(TRAY_FRAMES, key=lambda s: (abs(s - want), s))

def _frame_file(state, size):
    for d in _state["dirs"]:
        f = d / state / f"{size}.png"
        if f.is_file():
            return f
    return None

def get(state, size=None):
    """取状态帧（(state,size) 双键缓存；缺图回退 default，永不抛）。"""
    size = size or pick_size()
    key = (state, size)
    if key in _state["cache"]:
        return _state["cache"][key]
    f = _frame_file(state, size)
    if f is None and state != "default":
        if state not in _state["missed"]:
            print("tray_icons: state %r missing, falling back to default" % state)
            _state["missed"].add(state)
        return get("default", size)
    if f is None:
        if "default" not in _state["missed"]:
            print("tray_icons: no default asset found in %s" % _state["dirs"])
            _state["missed"].add("default")
        return Image.new("RGBA", (size, size), (64, 64, 64, 255))
    img = Image.open(f).convert("RGBA")
    _state["cache"][key] = img
    return img

def bind(pystray_icon, initial_state="default"):
    """绑定托盘图标对象并设初态（不触发 icon 渲染——icon 尚未 run 时由 pystray 自行处理）。"""
    _state["icon"] = pystray_icon
    _state["current"] = initial_state
    if pystray_icon is not None:
        pystray_icon.icon = get(initial_state)

def set_state(state):
    """切换托盘状态图（内部去重；同一状态重复调用零开销零闪动）。"""
    if state == _state["current"]:
        return False
    _state["current"] = state
    icon = _state["icon"]
    if icon is not None:
        icon.icon = get(state)
    return True
