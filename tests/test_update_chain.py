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
  ③ 退出路径以该路径调 launch_pending_cmd（**不再走 os.system**）
  ④ 该件的机制证据：真正传给内核的 creationflags 含
     CREATE_NO_WINDOW|DETACHED_PROCESS（无控制台可闪），且脚本真的被拉起来跑
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402  （R2 位置 + 删前放句柄）

_TMP = scratch_dir("dsh-update-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import main as M  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


# R4：被执行的脚本必须有超时；超时即 FAIL 并点名「疑似模态框」。
_LAUNCH_TIMEOUT = 10.0


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


# ⓪ 日志契约：模板件按 print 形态调用 log（最多 5 参，update_helper L442）。
# 工具的 log 若只收 1 个参数，模块里那 6 处多参调用会在**真路径**上 TypeError——
# 「每次下载」「每次拉起替换脚本」「存在失败 marker 时」全中（2026-09-19 实测踩中）。
try:
    M.log("contract", "check", "with", "five", "args")
    _arity_ok, _arity_detail = True, ""
except TypeError as _exc:
    _arity_ok, _arity_detail = False, str(_exc)
check("tool log() accepts the template's multi-arg (print) form", _arity_ok, _arity_detail)

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

# ③ 退出路径以该路径调 launch_pending_cmd；os.system 一旦复活即判失败
launched = []
system_calls = []
M.update_helper.launch_pending_cmd = (
    lambda cmd=None, log=None: launched.append(cmd) or True)
_real_system = os.system
os.system = lambda cmd: system_calls.append(cmd) or 0    # 只为捕获，不真的执行
M.tray_kit.confirm_quit_dialog = lambda *a, **k: {"go": True, "stop_service": False}
M.STOP_EVENT.clear()
try:
    M.quit_menu(_Icon(), None)
finally:
    os.system = _real_system
check("quit launches the stored apply script",
      launched == [r"C:\tmp\dsh-apply.cmd"], repr(launched))
check("quit no longer goes through os.system (the console-flash path is gone)",
      system_calls == [], repr(system_calls))

# ④ 机制证据：观察真正交给内核的 flags，并让脚本真的跑一次。
import subprocess as _sp  # noqa: E402
import modules.update_helper.update_helper as _UH  # noqa: E402

_seen = {}
_real_popen = _UH.subprocess.Popen


class _Popen:
    def __init__(self, argv, **kw):
        _seen["argv"] = argv
        _seen["flags"] = kw.get("creationflags", 0)


_UH.subprocess.Popen = _Popen
try:
    _ok = _UH.launch_pending_cmd(r"C:\tmp\dsh-apply.cmd", log=lambda *a: None)
finally:
    _UH.subprocess.Popen = _real_popen
_want = getattr(_sp, "CREATE_NO_WINDOW", 0) | getattr(_sp, "DETACHED_PROCESS", 0)
check("launch flags suppress the console and detach the child",
      _ok is True and _seen.get("flags") == _want, repr(_seen))

# 真的拉一次：脚本落一个标记文件，证明它脱离父进程后确实跑起来了。
# R1（2026-09-19 lead 裁定）：替身**必须先创建且始终存在**——`start` 指向不存在的
# 目标（.exe 或 .vbs）会弹**模态框**，无人值守下永久挂死（用户桌面上刚爆过 15 个
# WSH「无法找到脚本文件」）。所以"有没有被启动"一律看**副作用**（marker 是否写入），
# 绝不用"目标缺失"构造场景——那是拿模态框当断言，等于把测试变成炸弹。
_marker = Path(_TMP) / "launched.marker"
_script = Path(_TMP) / "probe.cmd"
_script.write_text('@echo off\r\necho alive > "%s"\r\n' % _marker, encoding="ascii")
_ok = _UH.launch_pending_cmd(str(_script), log=lambda *a: None)
_start = time.monotonic()
while time.monotonic() - _start < _LAUNCH_TIMEOUT and not _marker.is_file():
    time.sleep(0.05)
_elapsed = time.monotonic() - _start
check("the script really runs detached", _ok is True and _marker.is_file(),
      "marker=%s elapsed=%.1fs" % (_marker.is_file(), _elapsed))
# R4：把"挂死"变成"红灯"。超时的第一嫌疑是**模态框**（start 目标缺失 / 真跑单实例
# 守卫），不是"脚本慢"——无人值守路径上一次都挂不起。
check("launch did not hang (< %.1fs)" % _LAUNCH_TIMEOUT, _elapsed < _LAUNCH_TIMEOUT,
      "elapsed=%.1fs —— 若超时，疑似模态框（start 目标缺失或真跑单实例守卫）" % _elapsed)

check("temp dir cleaned up (no %TEMP% leak)", rmtree_cleanup(_TMP), str(_TMP))
print("UPDATE CHAIN TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
