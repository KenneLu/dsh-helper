# -*- coding: utf-8 -*-
"""Bug 1 修复回归：dsh 的 stdout/stderr 改**文件重定向 + tail**，不再用 PIPE。

背景：helper 用 ``stdout=subprocess.PIPE`` 启动 dsh；helper 一退出，管道读端
立即关闭，dsh（dsh.cmd → node.exe）下次写 stdout 就收到 ERROR_BROKEN_PIPE →
Node 抛未处理的 EPIPE 直接崩（ws-mcp 每 30 秒写重连失败日志必然触发）。

修复：stdout/stderr 重定向到 ``<LOG_DIR>\\dsh-output.log``，后台线程 tail 该文件。

本测试**不启动真实 dsh**：用 python 子进程扮演 dsh，验证生产代码的契约：

  ① 启动契约：stdout 不是 PIPE（``proc.stdout is None``），重定向到 dsh-output.log；
  ② tail 线程把 dsh 输出喂给 append_output，URL 仍被捕获；
  ③ EPIPE 回归：父端文件对象关闭（模拟 helper 退出）后，dsh 继续写文件不崩；
  ④ 对照：旧 PIPE 方案在同样时序下子进程写 stdout 会死（证明机制）。

"能红"对照：把 spawn_managed_dsh 的 stdout 改回 subprocess.PIPE，① 立即红。
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402

_TMP = scratch_dir("dsh-helper-epipe-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ.pop("DSH_HELPER_CONFIG", None)

import main  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + str(detail) if not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


def wait_for(predicate, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# 假 dsh：先打 URL 行，1 秒后再打第二行（模拟 helper 退出后 dsh 仍要写 stdout）。
FAKE_DSH = (
    "import sys, time\n"
    "print('dsh web: http://127.0.0.1:39999/', flush=True)\n"
    "time.sleep(1.0)\n"
    "print('second line after parent handle closed', flush=True)\n"
)
URL_LINE = "dsh web: http://127.0.0.1:39999/"
EXPECTED_URL = "http://127.0.0.1:39999/"
SECOND_LINE = "second line after parent handle closed"


# ---------------- ① 启动契约：stdout 重定向到文件，不是 PIPE ----------------
main.set_state(phase="starting", url="", pid=None, managed=True, last_output=[])
proc, log_path = main.spawn_managed_dsh([sys.executable, "-u", "-c", FAKE_DSH])
check("log path is <LOG_DIR>/dsh-output.log",
      log_path == main.LOG_DIR / "dsh-output.log", log_path)
check("stdout is NOT a pipe (proc.stdout is None)", proc.stdout is None, proc.stdout)
check("parent holds the redirected file object", main._DSH_OUTPUT_FILE is not None)

# 与 start_impl 同一条路径：tail 线程读新增输出。
tail = threading.Thread(target=main.tail_process_output, args=(log_path, proc), daemon=True)
tail.start()

try:
    got_url = wait_for(lambda: bool(main.state_copy().get("url")))
    check("tail captured the dsh URL line",
          got_url and main.state_copy()["url"] == EXPECTED_URL, main.state_copy().get("url"))
    check("tail fed append_output (last_output)",
          any(URL_LINE in line for line in main.state_copy().get("last_output", [])),
          main.state_copy().get("last_output"))

    # ---------------- ② EPIPE 回归：父端文件对象关闭（模拟 helper 退出） ----------------
    main._DSH_OUTPUT_FILE.close()
    rc = proc.wait(timeout=10)
    check("dsh survives write after helper-side handle closed (rc=0)", rc == 0, rc)

    text = log_path.read_text(encoding="utf-8", errors="replace")
    check("log file kept both lines", URL_LINE in text and SECOND_LINE in text, text[-200:])
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


# ---------------- ③ 对照：旧 PIPE 方案在同样时序下会死（证明机制） ----------------
p = subprocess.Popen([sys.executable, "-u", "-c", FAKE_DSH],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
first = p.stdout.readline()
p.stdout.close()  # 模拟 helper 退出关闭管道读端
rc = p.wait(timeout=10)
check("control: PIPE child dies on write after read end closed (rc != 0)", rc != 0, (first, rc))


check("temp dir cleaned up (no leak)", rmtree_cleanup(_TMP), str(_TMP))

print("DSH OUTPUT REDIRECT TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
