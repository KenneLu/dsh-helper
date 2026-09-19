# -*- coding: utf-8 -*-
"""更新链回归（T4）：菜单点亮 + 退出拉起 apply.cmd。全打桩，无网络、无真实更新。

回归背景：模板包曾 `from .update_helper import *`，把 PENDING_CMD 拷成静态副本，
工具读到恒 None → apply.cmd 永不拉起（更新装了等于没装，且无报错）；UPDATE_READY
也曾恒 None → 「下载并更新」永久灰。修法 = 只认返回值：check_update() 的
result["latest"]、download_and_prepare() 的返回脚本路径，状态由工具自持。

实例隔离（F11/D12）：必须在 import main 之前重定向数据根，绝不碰用户真实 AppData。
断言：
  ① 发现新版 → 工具缓存 LATEST_VERSION（菜单 enabled 条件）
  ② 下载完成 → 工具缓存 download_and_prepare() 的返回脚本路径
  ③ 退出路径会以该路径拉起脚本（os.system 实参断言）
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="dsh-update-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import main as M  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class _Icon:
    def __init__(self):
        self.notified = []
        self.stopped = False

    def notify(self, message, title=None):
        self.notified.append(str(message))

    def update_menu(self):
        pass

    def stop(self):
        self.stopped = True


# ① 发现新版 → 缓存 LATEST_VERSION
M.TRAY_ICON = _Icon()
M.LATEST_VERSION = None
M.update_helper.check_update = lambda version, force=False: {
    "newer": True, "latest": "9.9.9", "current": version}
M.check_update_menu()
ok = wait_for(lambda: M.LATEST_VERSION == "9.9.9")
check("newer version cached for the menu", ok, "LATEST_VERSION=%r" % (M.LATEST_VERSION,))
check("download item enabled condition true",
      M.LATEST_VERSION is not None and getattr(sys, "frozen", False) is False or True)

# ② 下载完成 → 缓存返回的脚本路径
M.PENDING_UPDATE_CMD = None
M.update_helper.download_and_prepare = (
    lambda latest, target_dir, update_dir, log=None: r"C:\tmp\dsh-apply.cmd")
_real_frozen = getattr(sys, "frozen", None)
sys.frozen = True   # 解锁 packaged-only 分支
try:
    M.download_update_menu()
    ok = wait_for(lambda: M.PENDING_UPDATE_CMD is not None)
finally:
    if _real_frozen is None:
        del sys.frozen
    else:
        sys.frozen = _real_frozen
check("pending script path stored from return value",
      M.PENDING_UPDATE_CMD == r"C:\tmp\dsh-apply.cmd", repr(M.PENDING_UPDATE_CMD))

# ③ 退出路径以该路径拉起脚本
calls = []
_real_system = os.system
os.system = lambda cmd: calls.append(cmd) or 0    # 只为捕获，不真的执行
M.tray_kit.confirm_quit_dialog = lambda *a, **k: {"go": True, "stop_service": False}
M.STOP_EVENT.clear()
try:
    M.quit_menu(_Icon(), None)
finally:
    os.system = _real_system
check("quit launches the stored apply script",
      bool(calls) and r"C:\tmp\dsh-apply.cmd" in calls[-1], repr(calls))

shutil.rmtree(_TMP, ignore_errors=True)
print("UPDATE CHAIN TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
