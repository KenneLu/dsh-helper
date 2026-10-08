# -*- coding: utf-8 -*-
"""tray_icons 静态贴图断言：状态定义 == 资产目录 + 帧在位 + 加载回退。
（与 l-s2t 同款；dsh 状态集 = {running, stopped} + default 兜底。）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from template.tray_icons import tray_icons  # noqa: E402
from template.appconfig import ICON_STATE_ARTISTS  # noqa: E402
from template.icons.icons import STATE_SIZES  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "resources" / "icons"

want = set(ICON_STATE_ARTISTS or {}) | {"default"}
have = {p.name for p in ASSET_DIR.iterdir() if p.is_dir()} if ASSET_DIR.is_dir() else set()
check("状态键集 == 资产目录状态集", want == have,
      "定义=%s 资产=%s 差=%s" % (sorted(want), sorted(have), sorted(want ^ have)))

missing = [f"{s}/{z}.png" for s in have for z in STATE_SIZES
           if not (ASSET_DIR / s / f"{z}.png").is_file()]
check("全套帧在位（%d 状态 × %d 档）" % (len(have), len(STATE_SIZES)), not missing, missing[:4])

dirs = tray_icons.init()
check("init 解析到资产目录", any(d == ASSET_DIR for d in dirs), [str(d) for d in dirs])
check("按档加载 running/32", tray_icons.get("running", 32).size == (32, 32))
check("缺图回退 default 永不崩", tray_icons.get("__nope__", 24).size == (24, 24))
check("set_state 去重", tray_icons.set_state("stopped") in (True, False) and not tray_icons.set_state("stopped"))

print("TRAY ICONS TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
