# -*- coding: utf-8 -*-
"""UI 队列封送。

tkinter 不是线程安全的，而对话框是在工作线程里弹的（为了不阻塞托盘）。封送把
**所有 Tk 工作收进同一个常驻线程**：跨线程只传「队列里的一个可调用对象」。

回归背景（2026-09-19 实测踩到）：第一版 `ui_post()` 只 put 不保证有人在消费——
线程由 main() 启动，而测试/诊断路径不经过 main()，于是 `test_update_chain` 直接调
`quit_menu()` 时**永久卡在 done.wait()**（测试挂死，比失败更糟）。所以这里专门钉两条：
  ① 没经过 main() 也必须能跑（线程按需懒启动）；
  ② 已经在 Tk 线程上时直接跑（否则自己投的活自己等 = 自锁）。

实例隔离：import main 之前重定向数据根。
"""
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402  （scratch 目录位置 + 删前放句柄）

_TMP = scratch_dir("dsh-ui-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import main as M  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def _guard(seconds, fn):
    """在独立线程里跑 fn 并限时等待——死锁要变成一条红断言，而不是把测试挂死。"""
    box = {}

    def _run():
        try:
            box["result"] = fn()
        except BaseException as exc:      # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return None, "TIMEOUT（死锁）"
    if "exc" in box:
        return None, "%s: %s" % (type(box["exc"]).__name__, box["exc"])
    return box.get("result"), ""


# ① 值能回来
res, err = _guard(10, lambda: M.ui_post(lambda: 6 * 7))
check("ui_post returns the callable's result", err == "" and res == 42, err or repr(res))

# ② 异常要原样回到调用方，不能吞
def _boom():
    raise ValueError("expected")


_res, err = _guard(10, lambda: M.ui_post(_boom))
check("ui_post propagates the exception to the caller",
      "ValueError: expected" in err, err)

# ③ 所有工作落在**同一个**线程上（这才是"唯一 Tk 线程"的意义）
seen = []
for _ in range(5):
    _res, err = _guard(10, lambda: M.ui_post(lambda: seen.append(threading.get_ident())))
check("all work runs on one dedicated thread",
      err == "" and len(set(seen)) == 1 and seen[0] != threading.get_ident(),
      "idents=%s main=%s" % (sorted(set(seen)), threading.get_ident()))

# ④ 从 Tk 线程内部再投一次不得自锁（直接执行）
_res, err = _guard(10, lambda: M.ui_post(lambda: M.ui_post(lambda: "nested")))
check("re-entrant ui_post from the UI thread does not deadlock", err == "" and _res == "nested",
      err or repr(_res))

# ⑤ 没经过 main() 也必须可用：上面几条已经在没有 main() 的前提下跑过了，
#    这里把"懒启动"这一事实显式断言出来（线程名就是那条判据）。
check("the UI thread is started on demand (main() never ran here)",
      any(t.name == M._UI_THREAD_NAME for t in threading.enumerate()),
      repr([t.name for t in threading.enumerate()]))

check("temp dir cleaned up (no %TEMP% leak)", rmtree_cleanup(_TMP), str(_TMP))
print("UI MARSHAL TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
