# -*- coding: utf-8 -*-
"""门禁自证：注入泄漏 → 红 → 回滚 → 绿（留记录用）。

跑法：``python tests/temp_gate_selfcheck.py``（退出码 0 = 自证通过）。

三拍：
1. **基线绿**：``--save`` 后记基线，再 check 应为 0（没漏）；
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
    root = Path(tempfile.gettempdir()) / temp_gate.temp_kit.APP_ID
    leak = root / _LEAK_NAME
    staging = Path(tempfile.gettempdir()) / (temp_gate.temp_kit.APP_ID + "-update-selfcheck")
    results = []

    # 0) update 暂存**不误报**（根级形态，真实形态）
    temp_gate.save()
    staging.mkdir(parents=True, exist_ok=True)
    rc = temp_gate.check()
    results.append(("update staging not flagged (expect 0)", rc, rc == 0))
    shutil.rmtree(staging, ignore_errors=True)

    # 1) 基线绿
    temp_gate.save()
    rc = temp_gate.check()
    results.append(("baseline green (expect 0)", rc, rc == 0))

    # 2) 注入红
    leak.mkdir(parents=True, exist_ok=True)
    rc = temp_gate.check()
    results.append(("leak injected -> red (expect 1)", rc, rc == 1))

    # 3) 回滚绿
    shutil.rmtree(leak, ignore_errors=True)
    rc = temp_gate.check()
    results.append(("rollback -> green (expect 0)", rc, rc == 0))

    ok = all(passed for _, _, passed in results)
    for name, rc, passed in results:
        print("[%s] %-34s rc=%d" % ("PASS" if passed else "FAIL", name, rc))
    print("GATE SELFCHECK " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
