# -*- coding: utf-8 -*-
"""托盘初态必须与当前业务状态同源（1.8.8 回归钉）。

背景（2026-10-26 实测）：启动顺序是 refresh_state() → 建托盘。若 dsh Web 已在运行，
refresh_state() 会在托盘创建前把 phase 置为 running，并把 running 签名记进 MENU_SIG；
等托盘建好后状态不再变化 ⇒ rebuild_menu 不会再跑。此时若初态写死 "stopped"，
图标会永久停在灰色 stopped 图，而菜单（lambda 实时求值）仍显示「运行中」。
1.8.5/1.8.6/1.8.7 都有此问题（启动顺序逐字相同）。

本测试钉住 `initial_tray_state()`：只有 phase == running 才用 running 图，
其余（stopped/starting/stopping/error）一律 stopped。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


for phase in ("running",):
    main.set_state(phase=phase)
    got = main.initial_tray_state()
    check("%s -> running icon" % phase, got == "running", got)

for phase in ("stopped", "starting", "stopping", "error"):
    main.set_state(phase=phase)
    got = main.initial_tray_state()
    check("%s -> stopped icon" % phase, got == "stopped", got)

print("TRAY INITIAL STATE TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
