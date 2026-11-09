# -*- coding: utf-8 -*-
"""临时目录泄漏门禁（build.bat 调用）——**工具侧薄封装**，逻辑在 ``template.temp_kit``。

跑测试前后扫 ``%TEMP%\\<APP_ID>*``，本次新增 = 0。用法：

    python tests/temp_gate.py --save      # 测试前：把当前条目记成基线
    python tests/temp_gate.py             # 测试后：与基线比对，有新增即 exit 1

为什么放独立脚本：build.bat 的测试门禁是「逐个 python 跑 test_*.py」，门禁本身
不能被那个循环当成测试跑（本文件名不以 test_ 开头）。

判据只认「本次跑测试**新增**的条目」——历史残留不算（那是存量清理的事），
避免把「过去漏的」误判成「这次漏的」。
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from template import temp_kit   # noqa: E402


def main() -> int:
    if "--save" in sys.argv:
        count = temp_kit.gate_save()
        print("[GATE] temp baseline saved: %%TEMP%%\\%s* (%d entries)" % (temp_kit.APP_ID, count))
        return 0
    ok, new = temp_kit.gate_check()
    if not ok:
        print("[ERROR] temp leak: %d new entr(y/ies) under %%TEMP%%\\%s*:"
              % (len(new), temp_kit.APP_ID))
        for name in new:
            print("         %s" % name)
        return 1
    print("[GATE] temp dir leak: 0 new entries under %%TEMP%%\\%s*" % temp_kit.APP_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
