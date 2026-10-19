# -*- coding: utf-8 -*-
"""日志初始化失败不得崩启动（1.8.4 启动热修回归）。

数据区 `log` 路径被另一个实例/升级窗口占用时，log_kit 的 mkdir/FileHandler 会抛；
旧版模块级 `LOG_DIR.mkdir(...) + log_kit.get_logger(...)` 没有兜底 → 导入即崩。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


def run_python(code, env):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


with tempfile.TemporaryDirectory(prefix="dsh-helper-logfail-") as temp:
    data = Path(temp) / "data"
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DSH_HELPER_DATA_DIR": str(data), "PYTHONUTF8": "1"}
    env.pop("DSH_HELPER_CONFIG", None)

    probe = run_python(
        "import sys; sys.path.insert(0, %r); "
        "from template.appconfig import APP_ID; "
        "from template.paths import LOG_DIR; print(str(LOG_DIR)); print(APP_ID)" % str(SRC),
        env,
    )
    check("probe: LOG_DIR resolved", probe.returncode == 0 and probe.stdout.strip(),
          (probe.returncode, probe.stdout, probe.stderr[-300:]))
    if probe.returncode != 0 or not probe.stdout.strip():
        print("LOG FAIL-OPEN TEST FAILED: " + ",".join(FAILS), flush=True)
        sys.exit(1)
    lines = probe.stdout.strip().splitlines()
    log_dir = Path(lines[0])
    app_id = lines[-1]
    log_dir.mkdir(parents=True, exist_ok=True)
    # 让日志文件本身无法打开（同名的目录占位）——目录本身可建，失败发生在 FileHandler。
    blocked = log_dir / (app_id + ".log")
    blocked.mkdir()
    check("setup: log file path is blocked by a directory", blocked.is_dir())

    code = (
        "import sys; sys.path.insert(0, %r); "
        "import main; "
        "print('IMPORT OK'); "
        "main.log('fallback', 'accepts', 3, 'parts')" % str(SRC)
    )
    result = run_python(code, env)
    combined = (result.stdout or "") + (result.stderr or "")
    check("import exits 0 with the log path blocked", result.returncode == 0,
          (result.returncode, combined[-500:]))
    check("module import completed", "IMPORT OK" in (result.stdout or ""), result.stdout[-300:])
    check("no uncaught traceback", "Traceback" not in combined, combined[-500:])
    check("failure was signalled on stderr", "log init failed" in (result.stderr or ""),
          (result.stderr or "")[-300:])

print("LOG FAIL-OPEN TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
