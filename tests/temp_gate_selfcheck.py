# -*- coding: utf-8 -*-
"""门禁自证：注入泄漏 → 红 → 回滚 → 绿（留记录用）。

跑法：``python tests/temp_gate_selfcheck.py``（退出码 0 = 自证通过）。

四拍：
0. **update 暂存不误报**：放一个打了标记的暂存目录，check 应为 0；
1. **基线绿**：``gate_save`` 后记基线，再 check 应为 0（没漏）；
2. **注入红**：往 ``%TEMP%\\<APP_ID>\\`` 塞一个标准命名的目录，check 必须为 1（能报红）；
3. **回滚绿**：删掉注入的目录，check 必须回到 0（能报绿）。

任一拍不符即 exit 1——门禁本身坏了（要么漏报、要么误报），必须修门禁而不是改测试。
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import temp_gate   # noqa: E402

_LEAK_NAME = "selfcheck-leak-20261109-120000-deadbe"


def main() -> int:
    kit = temp_gate.temp_kit
    temp = Path(tempfile.gettempdir())
    root = temp / kit.APP_ID
    leak = root / _LEAK_NAME
    staging = temp / (kit.APP_ID + "-update-selfcheck")
    results = []

    # 0) update 暂存**不误报**（标记文件识别）
    kit.gate_save()
    staging.mkdir(parents=True, exist_ok=True)
    kit.mark_staging(staging)
    ok, new = kit.gate_check()
    results.append(("update staging not flagged (expect 0)", ok, ok and not new))
    shutil.rmtree(staging, ignore_errors=True)

    # 1) 基线绿
    kit.gate_save()
    ok, _ = kit.gate_check()
    results.append(("baseline green (expect 0)", ok, ok))

    # 2) 注入红
    leak.mkdir(parents=True, exist_ok=True)
    ok, new = kit.gate_check()
    results.append(("leak injected -> red (expect 1)", not ok, not ok))

    # 3) 回滚绿
    shutil.rmtree(leak, ignore_errors=True)
    ok, _ = kit.gate_check()
    results.append(("rollback -> green (expect 0)", ok, ok))

    all_ok = all(passed for _, _, passed in results)
    for name, value, passed in results:
        print("[%s] %-34s -> %s" % ("PASS" if passed else "FAIL", name, value))
    print("GATE SELFCHECK " + ("OK" if all_ok else "FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
