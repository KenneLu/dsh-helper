# -*- coding: utf-8 -*-
"""托盘创建失败可读化（1.8.4）：不裸抛 traceback，弹可读中文 + 崩溃日志 + 退出码 2 的路径。

离线运行：fake Icon 抛 PermissionError、fake ctypes 记录 MessageBoxW、无头入口不弹窗。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
_TMP = tempfile.mkdtemp(prefix="dsh-helper-startup-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ.pop("DSH_HELPER_CONFIG", None)
sys.path.insert(0, str(SRC))

import main  # noqa: E402

# CI runner 的 Windows UI 语言是英文：本测试断言中文可读文案，先把界面语言钉成 zh。
# 产品自身的 en/zh 本地化正确性由 test_i18n 覆盖，不靠这条文案测试。
main.i18n.init("zh")

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


# ---------------- ① traceback 模块级可用 + 托盘失败包成可读错误 ----------------
check("module-level traceback is available", getattr(main, "traceback", None) is not None)


class _FailingIcon:
    def run(self, setup=None):
        raise PermissionError(5, "拒绝访问", "CreateWindowExW")


raised = None
try:
    main.run_tray_icon(_FailingIcon(), setup=None)
except Exception as exc:  # noqa: BLE001
    raised = exc
check("tray failure becomes TrayStartupError", isinstance(raised, main.TrayStartupError), repr(raised))

detail = "Traceback (most recent call last):\n  ...\nPermissionError: [WinError 5] 拒绝访问"
text = main._startup_failure_text(main.TrayStartupError(PermissionError(5, "denied")), detail)
check("readable text names the restricted-launch causes",
      "受限终端" in text and "文件资源管理器" in text, text)
check("readable text keeps the traceback tail", "PermissionError" in text, text)

saved_log_error = main.LOG_INIT_ERROR
main.LOG_INIT_ERROR = PermissionError("log dir denied")
try:
    with_log_note = main._startup_failure_text(main.TrayStartupError(PermissionError(5)), detail)
    check("text adds the log-directory note when logging also failed",
          "日志目录也不可写" in with_log_note, with_log_note)
finally:
    main.LOG_INIT_ERROR = saved_log_error

# ---------------- ② 崩溃日志 best-effort 多落点 ----------------
with tempfile.TemporaryDirectory(prefix="dsh-helper-crash-") as temp:
    root = Path(temp)
    saved_dirs = (main.LOG_DIR, main.APP_DIR)
    saved_temp = os.environ.get("TEMP")
    try:
        main.LOG_DIR = root / "log"
        main.APP_DIR = root / "app"
        first = main._write_startup_failure_log("boom-1")
        check("crash log written to the first available path",
              first is not None and "boom-1" in Path(first).read_text(encoding="utf-8"), first)

        blocker = root / "blocker"
        blocker.write_text("x", encoding="utf-8")
        main.LOG_DIR = blocker / "log"
        main.APP_DIR = blocker / "app"
        os.environ["TEMP"] = str(blocker)
        none_path = main._write_startup_failure_log("boom-2")
        check("all crash-log paths blocked -> None, no raise", none_path is None, none_path)
    finally:
        main.LOG_DIR, main.APP_DIR = saved_dirs
        if saved_temp is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = saved_temp

# ---------------- ③ MessageBoxW 兜底：失败不抛，无头入口不弹窗 ----------------
class _FakeUser32:
    def __init__(self, sink):
        self.sink = sink

    def MessageBoxW(self, hwnd, text, title, flags):
        self.sink.append((text, title, flags))
        return 1


class _FakeCtypes:
    def __init__(self, sink):
        self.windll = type("W", (), {"user32": _FakeUser32(sink)})()


boxes = []
saved_ctypes = main.ctypes
main.ctypes = _FakeCtypes(boxes)
try:
    check("MessageBoxW is invoked through the guard", main._message_box("hello", "title") is True and boxes,
          boxes)
finally:
    main.ctypes = saved_ctypes


class _BrokenCtypes:
    @property
    def windll(self):
        raise RuntimeError("no user32 here")


main.ctypes = _BrokenCtypes()
try:
    check("MessageBoxW failure returns False without raising", main._message_box("x") is False)
finally:
    main.ctypes = saved_ctypes

saved_argv = list(sys.argv)
saved_message_box = main._message_box
shown = []
main._message_box = lambda text, title=None: shown.append(text) or True
try:
    sys.argv = ["dsh-helper"]
    main._show_startup_failure(main.TrayStartupError(PermissionError(5)), detail)
    check("show-startup-failure routes to the messagebox", bool(shown), shown[:1])

    shown.clear()
    sys.argv = ["dsh-helper", "--smoke"]
    main._show_startup_failure(RuntimeError("boom"), detail)
    check("headless diagnostics mode does not pop a dialog", not shown, shown)
finally:
    sys.argv = saved_argv
    main._message_box = saved_message_box
    shutil.rmtree(_TMP, ignore_errors=True)

print("STARTUP GUARD TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
