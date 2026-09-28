# TEMPLATE-FROM: my-diy-tool-template/template/tray_icons/__init__.py | TEMPLATE-VER: 1.0.0
"""tray_icons 包门面：函数按引用绑定，无状态可复制（同 tray_kit 门面形态）。"""
from .tray_icons import (  # noqa: F401
    TRAY_FRAMES,
    bind,
    get,
    init,
    pick_size,
    set_state,
)

__all__ = ["TRAY_FRAMES", "bind", "get", "init", "pick_size", "set_state"]
