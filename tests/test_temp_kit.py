# -*- coding: utf-8 -*-
"""temp_kit 标准件回归：四层清理 / 崩溃日志 / update 暂存例外 / 边界。

对应执行文档【五】回归测试用例 4-9（硬崩溃残留、N=3、年龄上限、并发边界、误删边界、
update 暂存不被误删）。用例 1-3 由 build.bat 的门禁覆盖（跑测试前后扫新增）。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cleanup import rmtree_cleanup  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from template import temp_kit   # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def _dirs():
    root = temp_kit.temp_root()
    return [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []


# 用例从干净状态开始：清掉本工具临时根的既有内容（否则上一条用例的残留会让计数漂移）。
_root = temp_kit.temp_root()
if _root.is_dir():
    for _p in list(_root.iterdir()):
        rmtree_cleanup(_p)
    rmtree_cleanup(_root)


# ---- 用例 5：N=3 触发（建第 4 个时最旧的被删）----
built = [temp_kit.scratch_dir("gate-test") for _ in range(5)]
check("scratch_dir lands under %TEMP%/<APP_ID>/", built[0].parent == temp_kit.temp_root(),
      str(built[0]))
check("scratch_dir name = <scene>-<ts>-<rand>",
      len(built[0].name.split("-")) >= 4, built[0].name)
removed = temp_kit.sweep(keep=3, max_age=10 ** 9)
check("sweep keeps newest 3 (5 built -> 2 removed)", removed == 2, removed)
remaining = {p.name for p in _dirs()}
check("the 3 newest remain, the 2 oldest are gone",
      {p.name for p in built[2:]} <= remaining and not ({p.name for p in built[:2]} & remaining),
      sorted(remaining))

# ---- 用例 6：年龄上限触发（>24h 删，未超保留）----
old = temp_kit.scratch_dir("gate-old")
ancient = time.time() - 2 * 24 * 60 * 60
os.utime(old, (ancient, ancient))
fresh = temp_kit.scratch_dir("gate-fresh")
removed = temp_kit.sweep(keep=100, max_age=24 * 60 * 60)
check("sweep removes >24h only", removed == 1 and not old.exists() and fresh.exists(),
      (removed, old.exists(), fresh.exists()))

# ---- 用例 8：误删边界（非标准命名不动；%TEMP% 根下其他内容不动）----
staging = temp_kit.temp_root() / "dsh-helper-update-abc123"
staging.mkdir(parents=True, exist_ok=True)
temp_kit.sweep(keep=0, max_age=0)
check("update staging (<APP_ID>-update-*) NOT swept", staging.is_dir(), "was deleted")
stray = temp_kit.temp_root() / "not-a-scratch-dir"
stray.mkdir(parents=True, exist_ok=True)
temp_kit.sweep(keep=0, max_age=0)
check("non-standard entry NOT swept", stray.is_dir(), "was deleted")

# ---- 用例 4：硬崩溃残留被下次 sweep 清（进程被杀，退出钩子没跑）----
crashed = temp_kit.scratch_dir("gate-crash")
os.utime(crashed, (ancient, ancient))
removed = temp_kit.sweep(keep=0, max_age=24 * 60 * 60)
check("hard-crash residue swept on next sweep", removed >= 1 and not crashed.exists(),
      (removed, crashed.exists()))

# ---- 崩溃日志：一件一文件 + 保留 10 份 ----
crash_dir = temp_kit.temp_root() / "crash-test"
written = [temp_kit.write_crash_log("gate-test", "boom-%d" % i, directory=crash_dir)
           for i in range(12)]
check("crash log one-file-per-incident", all(w is not None for w in written), written[:2])
check("crash log keeps 10", len(list(crash_dir.iterdir())) == 10,
      len(list(crash_dir.iterdir())))
check("crash log name = <scene>-<ts>-<rand>.log",
      all(w.name.startswith("gate-test-") and w.name.endswith(".log") for w in written),
      written[0].name)
check("crash log content readable (rotated-out files are gone)",
      all("boom-" in w.read_text(encoding="utf-8") for w in written[-3:])
      and not written[0].exists(), "")

# ---- 清理 ----
for p in list(temp_kit.temp_root().iterdir()):
    if p.is_dir():
        rmtree_cleanup(p)
rmtree_cleanup(temp_kit.temp_root())
check("temp root cleaned up", not temp_kit.temp_root().exists(), str(temp_kit.temp_root()))

print("TEMP KIT TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
