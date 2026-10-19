# -*- coding: utf-8 -*-
"""重复启动静默化（1.8.4）：第二实例只写 show.request；常驻实例消费后弹气泡。

不启托盘 GUI：全部用 mock（notify 被替换成记录器）。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
_TMP = tempfile.mkdtemp(prefix="dsh-helper-show-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ.pop("DSH_HELPER_CONFIG", None)
sys.path.insert(0, str(SRC))

import main  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


saved_show_path = main.SHOW_REQUEST_PATH
saved_notify = main.notify
saved_tray = main.TRAY_ICON
try:
    with tempfile.TemporaryDirectory(prefix="dsh-helper-show-req-") as temp:
        main.SHOW_REQUEST_PATH = Path(temp) / "data" / "show.request"

        rc = main.request_show_existing()
        check("second instance exits with 0", rc == 0, rc)
        check("second instance writes show.request", main.SHOW_REQUEST_PATH.is_file(),
              main.SHOW_REQUEST_PATH)

        notes = []
        main.notify = lambda message: notes.append(message)
        consumed = main.consume_show_request()
        check("resident consumes show.request", consumed is True)
        check("show.request deleted after consumption", not main.SHOW_REQUEST_PATH.exists())
        check("resident shows the already-running balloon",
              notes == [main.i18n.t("dup_running_short")], notes)
        check("no request -> consume returns False", main.consume_show_request() is False)

        # 请求文件写失败也不能抛、第二实例仍静默退出 0
        blocker = Path(temp) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        main.SHOW_REQUEST_PATH = blocker / "show.request"
        raised = None
        try:
            rc2 = main.request_show_existing()
        except Exception as exc:  # noqa: BLE001
            raised = exc
        check("unwritable show.request still exits 0 silently", rc2 == 0 and raised is None,
              (rc2, raised))
finally:
    main.SHOW_REQUEST_PATH = saved_show_path
    main.notify = saved_notify
    main.TRAY_ICON = saved_tray
    shutil.rmtree(_TMP, ignore_errors=True)

print("SHOW REQUEST TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
