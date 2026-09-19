# -*- coding: utf-8 -*-
"""正常启动路径存活（D3-01 / SINGLE-04）：跑**真正的 main()**，只把重资源换成替身。

回归背景：`--smoke` 之类的诊断参数曾绕过正常启动路径，于是"冒烟全绿、工具其实
打不开"能长期共存。本测试走 main() 正常分支，断言守卫放行后启动序列真的推进：
日志出现 `startup`、`migrate_autostart` 被调用、命令检测被触发。

实例隔离（F11/D12）：import main 之前重定向数据根与配置，不碰用户真实 AppData。
守卫与重复启动提示被打桩（生产互斥体是内核对象，数据根隔离不了它；真守卫在用户
实例运行时返回 False 会弹**模态**框，测试会挂死——SINGLE-08）。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="dsh-startup-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ["DSH_HELPER_CONFIG"] = str(Path(_TMP) / "config.json")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pystray  # noqa: E402
import main as M  # noqa: E402
from modules.paths import LOG_PATH  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


CALLS = {"pending": 0, "autostart": 0, "detect": 0}


class _Icon:
    def __init__(self, *a, **k):
        self.visible = False
        self.menu = k.get("menu")

    def run(self, setup=None):
        if setup:
            setup(self)

    def stop(self):
        pass

    def update_menu(self):
        pass

    def notify(self, *a, **k):
        pass


# 守卫/重资源替身
M.tray_kit.acquire_single_instance = lambda *_a, **_k: True
M.tray_kit.warn_duplicate_instance = lambda *_a, **_k: None
M.pystray.Icon = _Icon
M.refresh_state = lambda: None
M.autostart.migrate_autostart = lambda **k: CALLS.__setitem__("autostart", CALLS["autostart"] + 1)
M.run_auto_detect_dsh_command = lambda startup=False: CALLS.__setitem__("detect", CALLS["detect"] + 1)
M.update_helper.check_update = lambda version, force=False: {"newer": False, "latest": "", "current": version}

rc = M.main()

text = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
check("main() did not short-circuit (guard passed)", rc is None, "rc=%r" % (rc,))
check("startup sequence reached (log has startup)",
      any("startup" in line for line in text.splitlines()), repr(text.splitlines()[:2]))
check("migrate_autostart called on startup", CALLS["autostart"] == 1, repr(CALLS))
check("command detection reached (dsh.cmd not configured)",
      CALLS["detect"] == 1, repr(CALLS))
check("log written inside the isolated data dir", str(LOG_PATH).startswith(_TMP),
      str(LOG_PATH))

shutil.rmtree(_TMP, ignore_errors=True)
print("STARTUP PATH TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
