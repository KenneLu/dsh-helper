# -*- coding: utf-8 -*-
"""构建工具的仓库根入口。

build.bat GATE 调 `python src\icons.py`：脚本目录（src）自动进 sys.path。
本文件为薄壳委派（不复制正本），正本升级即跟随。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from template.icons.icons import (  # noqa: F401
    STATE_SIZES,
    TASKBAR_SIZES,
    TRAY_SIZES,
    base_image,
    make_icons,
    make_state_icons,
    state_keys,
)

if __name__ == "__main__":
    base = str(Path(__file__).resolve().parent.parent)
    t, k = make_icons(base)
    states = make_state_icons(base)
    print("OK", t, k, "states:", {s: len(v) for s, v in states.items()},
          "exists:", Path(t).exists(), Path(k).exists())
