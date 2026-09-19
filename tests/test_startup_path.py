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
import time
from pathlib import Path
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402  （R2 位置 + 删前放句柄）

_TMP = scratch_dir("dsh-startup-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ["DSH_HELPER_CONFIG"] = str(Path(_TMP) / "config.json")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pystray  # noqa: E402
import main as M  # noqa: E402
from modules.paths import LOG_PATH  # noqa: E402
from modules.update_helper.update_helper import (  # noqa: E402
    TEMP_PREFIX,
    failed_marker_path,
    pop_failed_update_note as real_pop,
    sweep_stale_update_dirs as real_sweep,
)

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


# ---- 0) T4 两条自证：先验模板件的真实语义（此时还没打桩） --------------------
# sweep 只清一小时前的：伪造一个 TEMP 根，老目录该没、新目录和无关目录该留。
_fake_temp = Path(_TMP) / "fake-temp"
_fake_temp.mkdir()
_old = _fake_temp / (TEMP_PREFIX + "old")
_fresh = _fake_temp / (TEMP_PREFIX + "fresh")
_unrelated = _fake_temp / "unrelated-dir"
for _d in (_old, _fresh, _unrelated):
    _d.mkdir()
_now = time.time()
os.utime(_fresh, (_now, _now))
os.utime(_unrelated, (_now, _now))
os.utime(_old, (_now - 7200, _now - 7200))

_saved_tempdir = tempfile.tempdir
tempfile.tempdir = str(_fake_temp)   # 只改扫到哪个 TEMP 根，逻辑本身不动
try:
    _removed = real_sweep(max_age=3600.0)
finally:
    tempfile.tempdir = _saved_tempdir

check("sweep removes only stale update dirs (older than 1h)",
      _removed == 1 and not _old.exists() and _fresh.exists() and _unrelated.exists(),
      "removed=%s old=%s fresh=%s other=%s" % (_removed, _old.exists(),
                                               _fresh.exists(), _unrelated.exists()))

# 失败 marker 读一次即删：第二次启动不该再提示。
_home = Path(_TMP) / "marker-home"
_home.mkdir()
_upd = _home / "update-stage"
_upd.mkdir()
_marker = failed_marker_path(_upd)
_marker.write_text("rc=1 sha256 mismatch", encoding="utf-8")
_first = real_pop(_upd)
_second = real_pop(_upd)
check("failed marker reported once, then deleted (silent on 2nd start)",
      bool(_first) and not _marker.exists() and _second == "",
      "first=%r second=%r marker=%s" % (_first, _second, _marker.exists()))

# ---- 0b) C-2（paths 1.1.4，MUST-WIRE）：真语义 —— dev 态必须**不取句柄**且**说明原因** --
# 量的是模板件本身（跑在打桩之前，不是替身）。dev 态下"本实例"是 python.exe，给它加
# "不可删除"既无意义、又会让开发机的 Python 升级莫名失败；而**沉默地跳过**会让下一个人
# 以为保护生效了——所以理由必须落进日志。
_dev_log = []
_dev_ret = M.paths.hold_exe_delete_guard(log=_dev_log.append)
check("C-2 guard is a no-op in dev mode and says why (never silent)",
      _dev_ret is False and any("dev mode" in str(m) for m in _dev_log),
      "ret=%r log=%r" % (_dev_ret, _dev_log))


# ---- 1) 启动骨架：跑真正的 main()，重资源换替身 ------------------------------
CALLS = {"autostart": 0, "detect": 0}
ORDER = []          # T4 两条接线的调用顺序
NOTIFIES = []       # notify() 实际发出的文案


class _Icon:
    def __init__(self, *a, **k):
        ORDER.append("tray")      # 托盘创建这个**时点**要可见（C-2 的顺序断言要用）
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
# C-2（paths 1.1.4 MUST-WIRE）：守卫必须在**托盘创建之前**被调用——顺序就是那条契约
# 本身（README：晚一步，那一步的窗口期就没有保护）。替身签名**照抄生产**
# （`hold_exe_delete_guard(log=print)`）；写成 `lambda *a, **k` 会连"传错参数"也收下。
M.paths.hold_exe_delete_guard = lambda log=print: ORDER.append("guard") or True
M.refresh_state = lambda: None
M.autostart.migrate_autostart = lambda **k: CALLS.__setitem__("autostart", CALLS["autostart"] + 1)
M.run_auto_detect_dsh_command = lambda startup=False: CALLS.__setitem__("detect", CALLS["detect"] + 1)
M.update_helper.check_update = lambda version, force=False: {"newer": False, "latest": "", "current": version}
# T4 接线：清 TEMP 残包 + 取上次失败 marker。返回中文串（模板件），工具只用它的真值。
# 替身签名**照抄生产实现**（J 坑：替身比生产宽容 = 制造假绿）。生产是
# `sweep_stale_update_dirs(max_age=3600.0)` 与 `pop_failed_update_note(update_dir, log=...)`；
# 写成 `lambda *a, **k` 会连"传错参数"一起收下，等于把契约错误盖住。宁严勿宽。
M.update_helper.sweep_stale_update_dirs = (
    lambda max_age=3600.0: ORDER.append("sweep") or 3)
_REAL_POP = M.update_helper.pop_failed_update_note     # 留给下面的真 marker 端到端用例
M.update_helper.pop_failed_update_note = (
    lambda update_dir, log=lambda *a: None: ORDER.append("note") or "上次自动更新失败")
M.notify = lambda msg: NOTIFIES.append(msg)

rc = M.main()

text = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
check("main() did not short-circuit (guard passed)", rc is None, "rc=%r" % (rc,))
check("startup sequence reached (log has startup)",
      any("startup" in line for line in text.splitlines()), repr(text.splitlines()[:2]))
check("migrate_autostart called on startup", CALLS["autostart"] == 1, repr(CALLS))
check("command detection reached (dsh.cmd not configured)",
      CALLS["detect"] == 1, repr(CALLS))
check("startup wiring order: delete-guard, sweep, failed-note, then tray",
      ORDER == ["guard", "sweep", "note", "tray"], repr(ORDER))
check("failed-update note is surfaced through the i18n table",
      NOTIFIES == [M.i18n.t("notify_update_failed_prev")], repr(NOTIFIES))
check("log written inside the isolated data dir", str(LOG_PATH).startswith(_TMP),
      str(LOG_PATH))

# 程序本体目录只有一个来源（paths.APP_DIR）：main.py 曾自行再派生一份（开发态 = src/），
# 冻结态碰巧重合所以从未暴露；dev 下 ICON_ASSET 取不到、_load_icon_base() 静默走兜底图。
# §B1 dev 态锚定纪律要求路径断言落在测试里，而不是靠人记得。
from modules.paths import APP_DIR as _PATHS_APP_DIR  # noqa: E402
check("APP_DIR has a single source (paths, not a local re-derivation)",
      M.APP_DIR == _PATHS_APP_DIR, "%s vs %s" % (M.APP_DIR, _PATHS_APP_DIR))
check("icon asset resolves in dev mode", Path(M.ICON_ASSET).is_file(), str(M.ICON_ASSET))
# 同名派生量只许有一个来源：运行时解析的必须是 appconfig.ICON_ASSET 那一条
# （icons.py 构建期读的也是它），否则改了名字会 ico 变了、托盘图没变，静默分叉。
import modules.appconfig as _AC  # noqa: E402
check("ICON_ASSET derives from appconfig (single source of the relative path)",
      bool(_AC.ICON_ASSET) and Path(M.ICON_ASSET).as_posix().endswith(_AC.ICON_ASSET),
      "%s  <-  %s" % (M.ICON_ASSET, _AC.ICON_ASSET))

# ---- 真 marker 端到端（上面那条用的是替身，这里把它换回真函数）--------------
# 2026-09-19：这一跑当场抓到 `log` 契约冲突 —— 模板 update_helper 按 print 形态调用
# `log("previous update failed:", detail)`，而工具的 log 只收一个参数 ⇒ 只要失败 marker
# 存在，启动就 TypeError。替身永远测不出来，必须真跑一次。
from modules.update_helper.update_helper import failed_marker_path  # noqa: E402
from modules.paths import UPDATE_DIR as _UPDATE_DIR  # noqa: E402

_marker = failed_marker_path(_UPDATE_DIR)
_marker.parent.mkdir(parents=True, exist_ok=True)
_marker.write_text("rc=16 robocopy failed", encoding="utf-8")
M.update_helper.pop_failed_update_note = _REAL_POP
NOTIFIES.clear()
try:
    M.main()
except Exception as exc:                      # noqa: BLE001 - 失败要变成一条红断言
    check("real failed-marker start does not raise", False, "%s: %s" % (type(exc).__name__, exc))
_notified = [m for m in NOTIFIES if m == M.i18n.t("notify_update_failed_prev")]
check("real update.failed marker is surfaced on the next start",
      bool(_notified), repr(NOTIFIES))
check("real update.failed marker is consumed (read once, then deleted)",
      not _marker.is_file(), str(_marker))

NOTIFIES.clear()
try:
    M.main()
except Exception as exc:                      # noqa: BLE001
    check("second start with no marker does not raise", False, "%s: %s" % (type(exc).__name__, exc))
check("second start stays silent (read-once, no repeat nag)",
      M.i18n.t("notify_update_failed_prev") not in NOTIFIES, repr(NOTIFIES))

check("temp dir cleaned up (no %TEMP% leak)", rmtree_cleanup(_TMP), str(_TMP))
print("STARTUP PATH TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
