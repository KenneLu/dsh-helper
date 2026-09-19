# -*- coding: utf-8 -*-
"""语言切换必须**重建托盘菜单**（T1/D14）。

回归背景：模板 i18n 包曾 `from .i18n import *`，把 LANG 拷成静态副本——通知切到
英文，而菜单签名算出来不变 → 菜单永久中文。2.1.1 改为访问器 + PEP 562 委派。
本测试读**包命名空间**里的当前语言，并断言切换后菜单标签真的变成英文。

实例隔离：import main 之前重定向数据根；切换只写隔离区的 config.json。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="dsh-i18n-menu-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pystray  # noqa: E402
from modules import i18n  # noqa: E402
from modules.paths import CONFIG_PATH  # noqa: E402
import main as M  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def menu_labels(menu):
    out = []
    for item in menu.items:
        text = item.text
        if callable(text):
            try:
                text = text(item)
            except Exception:
                text = ""
        if isinstance(text, pystray.Menu):
            out.extend(menu_labels(text))
        elif text:
            out.append(str(text))
    return out


class _Icon:
    def __init__(self):
        self.menu = None
        self.updates = 0
        self.notified = []

    def update_menu(self):
        self.updates += 1

    def notify(self, message, title=None):
        self.notified.append(str(message))


icon = _Icon()
M.TRAY_ICON = icon

# 起点：中文菜单
i18n.init("zh")
check("starts in Chinese", i18n.current_lang() == "zh", i18n.current_lang())
check("Chinese menu contains 退出", "退出" in menu_labels(M.build_menu()))

# 切换 → 菜单必须被重建为英文
M.toggle_language(icon, None)
check("language switched to English (read via package)",
      i18n.current_lang() == "en", i18n.current_lang())
check("menu was rebuilt explicitly", icon.updates >= 1 and icon.menu is not None)
labels = menu_labels(icon.menu) if icon.menu is not None else []
check("rebuilt menu is English", "Quit" in labels and "退出" not in labels,
      repr(labels[:6]))
check("choice persisted to config.json",
      json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")).get("language") == "en")

# 切回中文也要生效
M.toggle_language(icon, None)
check("switch back to Chinese", i18n.current_lang() == "zh"
      and "退出" in menu_labels(icon.menu))

shutil.rmtree(_TMP, ignore_errors=True)
print("I18N MENU TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
