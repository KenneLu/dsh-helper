# -*- coding: utf-8 -*-
"""单实例守卫（CONFORMANCE SINGLE-01 / SINGLE-02 / SINGLE-07 / SINGLE-08）。

回归背景：命名互斥体名一旦含第二个反斜杠（`Local\\<app>\\SingleInstance`），
`CreateMutexW` 恒失败 err=3，而守卫把"创建失败"当成"已有实例" → 工具永远打不开。
`specific` 断言按 SINGLE-08 拆开：命名正确性用纯字符串 + 建/关不持有（用户实例
在跑时也能过），抢锁/拒绝用**测试专属名**（显式 mutex_name=），绝不占用生产名。

实例隔离：import main 之前重定向数据根；守卫是内核对象，数据根管不到它，
因此本测试不依赖生产锁是否空闲、也不碰它。
"""
import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402  （R2 位置 + 删前放句柄）

_TMP = scratch_dir("dsh-single-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from template import tray_kit  # noqa: E402
from template.appconfig import APP_ID  # noqa: E402

FAILS = []
EXPECTED_NAME = r"Local\%s-single-instance" % APP_ID
TEST_NAME = r"Local\%s-test-%d" % (APP_ID, os.getpid())


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def _quiet(_msg):
    pass


# ① 命名正确性（纯字符串 + 建/关不持有）
check("mutex name matches the family derivation",
      EXPECTED_NAME == r"Local\%s-single-instance" % APP_ID, EXPECTED_NAME)
check("mutex name has no second backslash", EXPECTED_NAME.count("\\") == 1, EXPECTED_NAME)
check("mutex name is Local-scoped", EXPECTED_NAME.startswith("Local\\"))
check("mutex name carries APP_ID", APP_ID in EXPECTED_NAME)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = ctypes.c_void_p
ctypes.set_last_error(0)
h = k32.CreateMutexW(None, False, EXPECTED_NAME)
accept_err = ctypes.get_last_error()
check("CreateMutexW accepts the derived name", bool(h) and accept_err in (0, 183),
      "err=%s" % accept_err)
if h:
    k32.CloseHandle(h)   # 只探测，不持有

# ② 共享探针 tray_kit.mutex_name_is_valid（smoke 与测试问同一个判据）
check("probe accepts the derived name", tray_kit.mutex_name_is_valid(APP_ID) is True)
check("probe rejects the old illegal name",
      tray_kit.mutex_name_is_valid(APP_ID, r"Local\%s\SingleInstance" % APP_ID) is False)

# ③ 旧非法名必须仍然失败（根因钉死）
ctypes.set_last_error(0)
bad = k32.CreateMutexW(None, False, r"Local\%s\SingleInstance" % APP_ID)
bad_err = ctypes.get_last_error()
check("old illegal name still fails", not bad and bad_err == 3, "err=%s" % bad_err)
if bad:
    k32.CloseHandle(bad)

# ③ 抢锁 / 拒绝：测试专属名，绝不占生产锁
check("first acquire passes",
      tray_kit.acquire_single_instance(APP_ID, mutex_name=TEST_NAME, log=_quiet) is True)
check("second acquire is refused",
      tray_kit.acquire_single_instance(APP_ID, mutex_name=TEST_NAME, log=_quiet) is False)

# ④ 失败方向：守卫自身出错必须放行
def _boom(*_a, **_k):
    raise OSError("simulated: kernel32 unavailable")


_real_win_dll = ctypes.WinDLL
ctypes.WinDLL = _boom
try:
    check("guard failure fails OPEN (never blocks startup)",
          tray_kit.acquire_single_instance(APP_ID, mutex_name=TEST_NAME, log=_quiet) is True)
finally:
    ctypes.WinDLL = _real_win_dll

check("temp dir cleaned up (no %TEMP% leak)", rmtree_cleanup(_TMP), str(_TMP))
print("SINGLE INSTANCE TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
