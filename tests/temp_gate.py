# -*- coding: utf-8 -*-
"""临时目录泄漏门禁（build.bat 调用）：跑测试前后扫 ``%TEMP%\\<APP_ID>*``，新增 = 0。

用法：
    python tests/temp_gate.py --save      # 测试前：把当前条目记成基线
    python tests/temp_gate.py             # 测试后：与基线比对，有新增即 exit 1

为什么放独立脚本：build.bat 的测试门禁是「逐个 python 跑 test_*.py」，门禁本身
不能被那个循环当成测试跑（本文件名不以 test_ 开头）。

判据只认「本次跑测试**新增**的条目」——历史残留不算（那是存量清理的事），
避免把「过去漏的」误判成「这次漏的」。
"""
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from template import temp_kit   # noqa: E402

BASELINE = Path(__file__).resolve().parents[1] / "build" / "temp-baseline.txt"


def _entries():
    """扫 `%TEMP%\\<APP_ID>*`——**不只扫 `%TEMP%\\<APP_ID>\\`**。

    旧形态的残留是**根级**的 `%TEMP%\\<APP_ID>-<随机>`（目录或文件），只扫子目录会漏掉。
    但 `%TEMP%\\<APP_ID>` **目录本身**不是泄漏（它是本工具的临时根），要排除。
    **`<APP_ID>-update*` 是 update 暂存**（归 update_helper 管），**不算泄漏**，排除。
    子目录内的条目加 `<APP_ID>/` 前缀，避免与根级旧形态同名混淆。
    """
    temp = Path(tempfile.gettempdir())
    prefix = temp_kit.APP_ID
    out = set()
    root = temp / prefix
    try:
        if root.is_dir():
            for p in root.iterdir():
                if temp_kit.is_update_staging(p.name):
                    continue
                out.add("%s/%s" % (prefix, p.name))
        for p in temp.iterdir():
            if p.name != prefix and not temp_kit.is_update_staging(p.name) and p.name.startswith(prefix):
                out.add(p.name)
    except OSError:
        pass
    return sorted(out)


def save():
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text("\n".join(_entries()), encoding="utf-8")
    print("[GATE] temp baseline saved: %%TEMP%%\\%s* (%d entries)"
          % (temp_kit.APP_ID, len(_entries())))


def check():
    before = set(BASELINE.read_text(encoding="utf-8").splitlines()) if BASELINE.exists() else set()
    after = _entries()
    new = sorted(set(after) - before)
    if new:
        print("[ERROR] temp leak: %d new entr(y/ies) under %%TEMP%%\\%s*:"
              % (len(new), temp_kit.APP_ID))
        for name in new:
            print("         %s" % name)
        return 1
    print("[GATE] temp dir leak: 0 new entries under %%TEMP%%\\%s*" % temp_kit.APP_ID)
    return 0


if __name__ == "__main__":
    if "--save" in sys.argv:
        save()
        raise SystemExit(0)
    raise SystemExit(check())
