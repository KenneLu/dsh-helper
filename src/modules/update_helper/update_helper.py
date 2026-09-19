# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/update_helper/update_helper.py | TEMPLATE-VER: 1.4.4
"""T4｜在线更新三段式：查（GitHub Releases）→ 下（zip + sha256）→ 换（退出后铺目录并重启）。

**基准**：本件按用户仲裁规则（STANDARDS B4）以 reme-helper 的**已验证更新链**为准
（`reme-helper/src/main.py` 7098-7345；语义清单见 `reme-helper/UPDATE-CHAIN-REFERENCE.md`），
不以"用的人多"为准。

1.4.4（2026-09-19）：**两处修复**（同一版正式注记，避免“同名异实”）。
  * **清扫缺陷**：`TEMP_PREFIX` 去掉尾随 `-`（旧值 `f"{APP_ID}-update-"` **永远匹不到** `…-update.bat`）+ 清扫体按类型分流（`is_dir()`→`rmtree`，否则 `unlink`）——旧实现只处理目录，于是**中断的更新会永久留下一个脚本文件**，每中断一次多一个且**无声**。
  * **同源修复**：`budget_s` 必须由**调用方那个 `limit`** 派生，
不许传模块常量。旧写法传 `UPDATE_WAIT_BUDGET_S`（按 `UPDATE_WAIT_LIMIT=120` 算），于是
调用方传 `limit=2` 时渲染出 `2 polls x 1000ms (nominal budget 120s)`——分母与数量级
自相矛盾，而且它只出现在**日志**里，不会让任何门禁变红。现为
`budget_s=limit * UPDATE_WAIT_TICK_MS // 1000`。`UPDATE_WAIT_BUDGET_S` 保留为
"默认 limit 下的名义预算"，**不再被渲染器使用**。
（本条可机械判据化：渲染 `limit=2` 必须出现 `budget 2s`。判据落点在 `sync_check --selftest`
的 update_helper 组，不在本文件。）

1.4.3（2026-09-19）：**两处修复**（都可机械判据化，见 conformance_check C-32/C-33）：

  * **等待节拍不再用 `ping`**：`ping -n 2 127.0.0.1` 看着像“睡 1 秒”，在**丢弃 loopback ICMP**
    的机器上实测 **9.0s/拍**（两次 4.5s 超时）
    ——名义 120s 的等待变成 ~18 分钟，而 `:giveup` 还打印 `after 120s`（**日志说谎**）。
    改成 `powershell -NoProfile -Command "Start-Sleep -Milliseconds {tick_ms}"`（ICMP-free；
    DETACHED 进程无 console，子进程不弹窗），常量拆成 `UPDATE_WAIT_LIMIT`（**轮询次数**）×
    `UPDATE_WAIT_TICK_MS`（每拍毫秒），超时行报告
    `%tries% polls x {tick_ms}ms (...) lower bound`——**不再打印没人量过的"秒"**。
  * **`pop_failed_update_note` 改成"先报告、最后删证据"**（C-32）：旧顺序在 `unlink` 抛
    OSError 时走 except 直接 `return ""`，detail 明明读到了用户却看不到提示；调用边界上的
    错误（arity/签名不符）更是连 `except OSError` 都拦不住。删不掉就留给下次再报。

1.4.2（2026-09-19）：补**第三处** `start` 的守卫（`:install_failed` 回铺之后）。前三轮只盯了
"暂存包为空"与"拷完缺 exe"两条路，而**回铺成功（rc<8）也可能没铺出 exe**（快照本身就缺）——
`start` 于是指向不存在的文件 ⇒ 同一个模态框卡死。**每一条通往 `start` 的分支都要自己验一次**，
别处有守卫不构成这条分支的证明。

1.4.1（2026-09-19）：`:stage_invalid`（暂存包里没有 exe）改为走 **`:cleanup_keep`**——
与 `:install_failed` 同一条规则：**失败要保留现场**。此前它走 `:cleanup`，把可疑的暂存整包删掉了，
只剩 marker 与日志；现场（"到底暂存了什么"）恰恰是人工排查最想要的东西。只多占一份暂存，
启动期的 `sweep_stale_update_dirs()` 会在一小时后回收。

1.4.0（2026-09-19）：照该基准清单 §9 第 1、2 条，把两件事做成**结构上不可能再犯**——
而不是继续靠约定挡着：

  * **状态改为"可变容器就地改 + 只读派生"**（STANDARDS §D6）。1.3.0 仍是
    `UPDATE_READY = None` 这类**模块级标量 + 函数内 `global` 重绑**——正是两次静默失效
    （`i18n.LANG`、`PENDING_CMD`）的形态；1.3.0 只是靠"包门面改用 `__getattr__` 委派"
    挡着，谁把 `import *` 加回来就又中招。现在：**唯一写入点**是 `_PUBLISHED` dict 的
    就地赋值，模块级**不存在**可被重绑的标量；`UPDATE_READY` / `PENDING_CMD` 由
    **PEP 562 `__getattr__` 派生**（读得到、永远是活值、无法被重新绑定）。
  * **新增 `launch_pending_cmd()`**：以 `CREATE_NO_WINDOW | DETACHED_PROCESS` 拉起替换脚本
    （reme 形态，`main.py:7258-7260`）。此前"怎么拉起"留给消费方，工具就各写一遍
    `os.system('start "" /min ...')`——**bat 必须在主进程退出后继续跑**，且不该在用户桌面
    闪控制台；这段语义属于本模块。

1.3.0（2026-09-19）：补上 1.2.0 漏掉的**回退与失败可见性**三件——1.2.0 只搬了"等待/上限/
备份/清扫"，仍缺替换失败后的活路：

  * **快照在替换之前、且只在替换成功后才轮转为 BACKUP**。1.2.0 是"先删旧 BACKUP → 再拷
    当前安装目录 → 再铺新版"，等于**新版还没落地就把回退源拆了**；robocopy 一旦失败无处可退。
    reme 的做法：先把当前安装目录快照成 `_backup.pre`，拷贝成功（rc<8）才 `move` 成 `_backup`。
  * **检查 robocopy 退出码（>=8 即失败）**：失败时**绝不启动新 exe**，而是从快照回铺并启动
    旧版本；若回铺再失败则**不启动任何 exe**、把快照留在原地供人工恢复。
  * **失败 marker**（`update.failed`）：由 bat 写入，主程序下次启动读一次、转成人话、删除
    （`pop_failed_update_note()`）——此时托盘已退出，只能下次说。
  * **sha256 缺失即中止**（`verify_zip_sha256`，fail-closed）。1.2.0 的"没有 .sha256 就跳过
    校验"是**静默放行**，reme 已把"期望值缺失"明确判为失败（安全缺口）。四工具 release.yml
    均已产出 `.zip.sha256`，故不会因此断更新。

1.2.0（2026-09-19）：**语义照 reme-helper 已验证的更新链重写**（1.0.x 是合成件——从未端到端
验证过，两处静默失效即由此而来：`PENDING_CMD` 被包 `import *` 复制、`UPDATE_READY` 从不赋值）。
搬入的语义要点：

  * 替换脚本**以镜像名等待旧进程真正退出**（`tasklist` 写文件 + `find` 读文件，**刻意不用管道**
    ——DETACHED 无 console 时 `tasklist | find` 永不返回，实测 0.13s vs 永久挂起）；等待有
    **上限**（`UPDATE_WAIT_LIMIT`），超时走 `:giveup` 记日志而非无限等；
  * 备份放在目标目录**之外**（否则 `robocopy /purge` 会把备份一起删，且
    `robocopy target target\\_backup /e` 会扫到自己的输出）；
  * 用 `robocopy /e /purge` 清掉上一版**残留文件**；全流程写 `update.log`；
    `:cleanup` **成功与放弃两条路径都走**，负责删暂存（实测每次约 50MB）并自删脚本；
  * `sweep_stale_update_dirs()`：清扫被中断的更新遗留在 %TEMP% 的整包，**只清一小时前的**；
  * `http_error_hint()`：403/429 是 GitHub **匿名配额**（每出口 IP 每小时 60 次，与同网其他
    工具共享），翻成人话而不是裸 `HTTP Error 403`。

包门面 `__init__.py` 不复制状态（见其 docstring）；状态经 `update_ready()` / `pending_cmd()` 读取。
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from modules.appconfig import APP_ID, EXE_NAME, REPO_NAME, REPO_OWNER

REPO = f"{REPO_OWNER}/{REPO_NAME}"
# 检查节流窗口（进程内：见 check_update 的说明——跨进程不生效，别把它当持久化配额保护）
CHECK_INTERVAL = 24 * 3600

# 等旧进程退出的上限：**轮询次数** × **每拍毫秒**（两个数必须一起看，别把次数当秒）。
#   `UPDATE_WAIT_LIMIT`   = 轮询次数
#   `UPDATE_WAIT_TICK_MS` = 每拍睡眠毫秒（脚本里由 `powershell Start-Sleep` 实现）
#   `UPDATE_WAIT_BUDGET_S`= 名义预算 = 次数 × 每拍 ÷ 1000（**下界**：每拍还要付 PowerShell 启动费）
# ⚠️ 2026-09-19 缺陷（五份都在）：节拍原是 `ping -n 2 127.0.0.1`，本意"睡 1 秒"，在**丢弃
# loopback ICMP** 的机器上实测 **9.0 s/拍**（两次 4.5s 超时）→ 名义 120s 实际约 18 分钟，
# 而 `:giveup` 还打印 "after 120s"——**日志说谎**。教训：等待/超时的单位假设**必须实测量过**；
# 禁止 ping 当节拍已升级为机械判据 C-33。
UPDATE_WAIT_LIMIT = 120
UPDATE_WAIT_TICK_MS = 1000
# 默认 limit 下的名义预算。**渲染器不得直接用它**（1.4.4 修复）：调用方可以传别的 limit，
# 用了这个常量就会渲染出"2 polls x 1000ms (nominal budget 120s)"这种不可能的日志。
# 渲染侧一律 `limit * UPDATE_WAIT_TICK_MS // 1000`，与 limit 同源。
UPDATE_WAIT_BUDGET_S = UPDATE_WAIT_LIMIT * UPDATE_WAIT_TICK_MS // 1000
# %TEMP% 下本应用自己的名字：**前缀到 `-update` 为止**（**不带**尾随分隔符），因为两类
# 产物都以它开头：
#   * 暂存目录 `<APP_ID>-update-<随机>`（mkdtemp 产生）
#   * 替换脚本 `<APP_ID>-update.bat`（固定名）
# ⚠️ 2026-09-19 缺陷（helpers-dev 找到）：旧值 `f"{APP_ID}-update-"` 带尾随 `-`，
# `glob` **永远匹配不到** `…-update.bat`；而清扫又只处理 `is_dir()` ⇒ **中断的更新会永久
# 留下一个 `*-update.bat`**，清扫器永远看不见（每中断一次多一个，且**无声**）。
TEMP_PREFIX = f"{APP_ID}-update"
# 更新器失败时留的 marker 文件名（落在 update_dir 的父目录，即用户数据区）
FAILED_MARKER_NAME = "update.failed"

# 「查」的节流状态（就地改，见 check_update）
_STATE = {"checked_for": "", "latest": "", "at": 0.0}
# 「对外可见状态」的唯一写入点（STANDARDS §D6）。
# 刻意**不做**模块级标量：标量一旦被函数内 `global` 重绑，外部经包命名空间读到的就是死副本
# （i18n.LANG / PENDING_CMD 两次静默失效）。这里只就地改 dict，读取一律走访问器或下面的
# __getattr__ 派生——结构上不存在"可被重绑的标量"，旧缺陷无法复现。
_PUBLISHED = {
    "ready": None,        # 有新版时的版本号；None=无（控制「下载并更新」菜单可用性）
    "pending_cmd": None,  # 已就绪的一次性替换脚本路径；None=无（控制退出时是否拉起）
}


def __getattr__(name):
    """PEP 562：`UPDATE_READY` / `PENDING_CMD` 由此**派生**，模块里没有这两个全局。

    关键性质：**`from .update_helper import *` 不会搬运它们**（`import *` 只搬运 `__dict__`
    里的名字，`__getattr__` 的派生名不在其中）——于是"包门面 import * 拷出死副本"这条路径
    在结构上被切断：拿不到旧值，只会明确报"没有该属性"。

    保留这两个名字只为兼容旧读法（README 曾承诺）。⚠️ 诚实边界：模块属性**仍可被显式赋值**
    遮蔽（`update_helper.UPDATE_READY = x` 不会报错——PEP 562 只管查找，不管赋值），
    那是**蓄意的重新绑定**，不是本缺陷要防的静默拷贝；真值仍只有一个写入点 `_PUBLISHED`。
    新代码请用 `update_ready()` / `pending_cmd()`。
    """
    if name == "UPDATE_READY":
        return _PUBLISHED["ready"]
    if name == "PENDING_CMD":
        return _PUBLISHED["pending_cmd"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 替换脚本模板。**ASCII-only**（cmd.exe 按机器 ANSI 代码页解析；解释一律留在 Python 侧）。
# 刻意**不用括号块**：cmd 对块内 errorlevel 的解析不可靠，全程 goto（reme 实测结论）。
# 三段标签与 reme 一致：`:gone`（铺新）→ `:install_failed`（回退）→ `:install_dead`（回退也
# 失败，不启动）；收尾分 `:cleanup`（成功/放弃，删暂存）与 `:cleanup_keep`（留现场供恢复）。
_APPLY_BAT = r"""@echo off
setlocal
set "TARGET={target}"
set "STAGE={stage}"
set "WORK={work}"
set "BACKUP={backup}"
set "SNAPSHOT={snapshot}"
set "FAILED={failed}"
set "LOG={log}"
echo [{stamp}] start target=%TARGET% backup=%BACKUP% >> "%LOG%"
set "POLL=%LOG%.poll"
set /a tries=0
:wait
rem NO PIPE HERE, on purpose: this script is spawned with DETACHED_PROCESS and has
rem no console; in that context "tasklist | find" NEVER RETURNS (find blocks on
rem stdin forever) and the update silently never happens. tasklist writes to a file
rem and find reads that file instead. (reme-helper: measured 0.13s vs hang.)
rem
rem %SystemRoot%\System32 ON EVERY EXTERNAL COMMAND BELOW - never the bare name.
rem PATH is not ours to assume. Measured on this machine: `where find` ->
rem H:\Tools\Git\usr\bin\find.exe FIRST (PATH index 5) and C:\Windows\System32
rem only at index 13. GNU find reads "/i" and the image name as PATH arguments, so
rem it exits 1 for every input - the "if errorlevel 1 goto gone" below then fires on
rem the FIRST tick, %tries% stays 1, :giveup is unreachable, and the wait loop never
rem waits. The update then copies over a binary that is still running. Measured in
rem real cmd.exe: pipe form, file-hit form and file-miss form all return 1.
rem Same trap applies to ping (-n means "numeric" to iputils). reme-helper has
rem carried this rule since its own update chain was written; this file was behind.
%SystemRoot%\System32\tasklist.exe /fi "imagename eq {exe}" /nh > "%POLL%" 2>nul
%SystemRoot%\System32\find.exe /i "{exe}" "%POLL%" >nul
if errorlevel 1 goto gone
set /a tries+=1
if %tries% geq {limit} goto giveup
rem Sleep one tick. NOT `ping -n 2 127.0.0.1`: that idiom means "1 second" only when
rem loopback ICMP answers - on a machine that drops it, each tick costs 2 x 4.5s
rem timeout = 9.0s (measured), so a nominal 120s budget really took ~18 minutes
rem while the log still said "120s". Start-Sleep is ICMP-free; it does pay a
rem PowerShell startup (~0.3s here, measured 1.29s per 1000ms tick), which is why
rem the timeout line below reports a lower bound instead of a precise total.
rem Spawned from a DETACHED_PROCESS bat, so the child has no console and no window
rem appears (same reason `tasklist`/`find` in this file stay windowless).
rem Absolute path for the same reason as the two lines above: never trust PATH.
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "Start-Sleep -Milliseconds {tick_ms}" >nul 2>nul
goto wait
:gone
rem Guard the staged package BEFORE touching the install dir. An empty/exe-less STAGE can
rem come from a swept %TEMP% (sweep_stale_update_dirs only clears >1h old dirs, but a reboot
rem or a manual cleanup can still take it): robocopy would then return 0-7 with /purge and
rem WIPE the install dir, and the "start" below would pop a modal error box for a missing
rem exe - a dialog nobody can dismiss, so the bat hangs forever holding the lock.
if not exist "%STAGE%\{exe}" goto stage_invalid
rem Snapshot the CURRENT install first. The previous BACKUP is NOT deleted here: it is the
rem rollback source and is only rotated AFTER a copy that succeeded.
if exist "%SNAPSHOT%" rmdir /s /q "%SNAPSHOT%"
%SystemRoot%\System32\Robocopy.exe "%TARGET%" "%SNAPSHOT%" /e /njh /njs /nfl /ndl >nul
echo [{stamp}] snapshot rc=%ERRORLEVEL% >> "%LOG%"
rem /purge removes files the previous version left behind.
%SystemRoot%\System32\Robocopy.exe "%STAGE%" "%TARGET%" /e /purge /njh /njs /nfl /ndl >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [{stamp}] copied rc=%RC% >> "%LOG%"
if %RC% geq 8 goto install_failed
if not exist "{newexe}" goto install_failed
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
move /y "%SNAPSHOT%" "%BACKUP%" >nul 2>nul
start "" "{newexe}"
echo [{stamp}] done >> "%LOG%"
goto cleanup
:stage_invalid
rem Nothing was touched yet: bring the CURRENT version back and say why. Starting a
rem non-existent exe is the one thing that must never happen (modal box -> hang).
> "%FAILED%" echo update failed {stamp}: staged package has no {exe}
echo [{stamp}] STAGE INVALID - no {exe} in stage; install untouched >> "%LOG%"
if exist "{newexe}" start "" "{newexe}"
rem KEEP the scene (goto cleanup_keep, not cleanup): same rule as :install_failed.
rem The staged package looked wrong - a human may want to see WHAT was staged before
rem the 1h sweep reclaims it. Only the poll file and this script are removed.
goto cleanup_keep
:install_failed
rem robocopy: 0-7 = success, >=8 = failure. On failure NEVER start the new exe; restore the
rem previous version from the snapshot so the tool comes back, and leave a marker for the app.
> "%FAILED%" echo update failed {stamp}: install rc=%RC%
echo [{stamp}] INSTALL FAILED rc=%RC% - restoring from snapshot >> "%LOG%"
%SystemRoot%\System32\Robocopy.exe "%SNAPSHOT%" "%TARGET%" /e /purge /njh /njs /nfl /ndl >> "%LOG%" 2>&1
if errorlevel 8 goto install_dead
rem rc<8 means "the restore did not error", NOT "the exe is now there" - the snapshot
rem itself can be missing it. EVERY path that reaches a start must verify on its own:
rem a guard elsewhere does not prove this branch. Without this line this is a third
rem "start on a missing exe -> modal box -> hang" entry point (reviewer, 2026-09-19).
if not exist "{newexe}" goto install_dead
echo [{stamp}] restored - starting previous version >> "%LOG%"
start "" "{newexe}"
goto cleanup_keep
:install_dead
echo [{stamp}] RESTORE FAILED - not starting; snapshot kept at %SNAPSHOT% >> "%LOG%"
goto cleanup_keep
:giveup
rem Report the poll count and the per-tick sleep, not a fake "seconds" figure: the
rem real elapsed time is >= {limit} x {tick_ms}ms because every tick also pays a
rem PowerShell startup. The old wording said "after 120s" while it had actually
rem been waiting ~18 minutes - a log that lies is worse than no log.
echo [{stamp}] aborted: {exe} still running after %tries% polls x {tick_ms}ms (nominal budget {budget_s}s, lower bound) >> "%LOG%"
goto cleanup
:cleanup
rem Runs on the success path: without it the staged package (~50MB/update) stays behind
rem forever. The script itself lives in %TEMP% (NOT in STAGE), so removing STAGE cannot
rem lock the running file.
if exist "%WORK%" rmdir /s /q "%WORK%"
goto cleanup_tail
:cleanup_keep
rem Keep WORK and SNAPSHOT for manual recovery; the marker makes the app notify next start.
goto cleanup_tail
:cleanup_tail
del "%POLL%" >nul 2>nul
(goto) 2>nul & del "%~f0"
"""


def _tag_to_version(tag):
    return tag[1:] if tag.startswith("v") else tag


def _version_is_newer(latest, current):
    def nums(v):
        return [int(x) for x in v.split(".") if str(x).isdigit()]
    try:
        return nums(latest) > nums(current)
    except Exception:
        return False


def update_ready():
    """有新版时的版本号，否则 None（**推荐读法**）。"""
    return _PUBLISHED["ready"]


def pending_cmd():
    """已就绪的一次性安装脚本路径，否则 None（**推荐读法**）。"""
    return _PUBLISHED["pending_cmd"]


def launch_pending_cmd(cmd=None, log=lambda *a: None):
    """在退出收尾处拉起替换脚本，返回是否已拉起。

    必须**无控制台且脱离父进程**（`CREATE_NO_WINDOW | DETACHED_PROCESS`，reme 形态
    `main.py:7258-7260`）：本进程马上就要退出，bat 要活到替换完成；而它若挂着一个控制台，
    用户桌面会闪黑框。这段语义属于本模块——不要让每个工具各写一遍 `os.system('start ...')`。

    调用方看着 True 再退（同 reme 的"交出更新后必退"）。
    """
    cmd = cmd or _PUBLISHED["pending_cmd"]
    if not cmd or os.name != "nt":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(["cmd.exe", "/c", str(cmd)], creationflags=flags, close_fds=True)
    except OSError as exc:
        log("launch pending failed:", exc)
        return False
    log("pending update launched:", str(cmd))
    return True


def http_error_hint(exc):
    """把 GitHub 的匿名配额拒绝翻成用户能行动的话（403/429），其余情况给空串。

    匿名访问 api.github.com 是**每出口 IP 每小时 60 次**，且与同一网络下的其他工具
    **共用**这份配额；用完返回 403，而 `str(exc)` 只会说 "HTTP Error 403: rate limit
    exceeded"，对用户没有任何行动指引（reme-helper 本机实测踩到过）。
    """
    if getattr(exc, "code", None) in (403, 429):
        return ("（这是 GitHub 的匿名访问配额，同一网络下的其他工具也会消耗它；"
                "过几分钟再试即可，不是配置问题）")
    return ""


def _publish(latest, current_version):
    """把一次成功的检查结果发布到模块状态，返回 newer。

    只在**成功取到 latest** 时调用：网络失败不误清已发现的新版。
    无新版（或 latest 为空）时置回 None——"下载并更新"据此保持灰态。
    """
    newer = bool(latest) and _version_is_newer(latest, current_version)
    _PUBLISHED["ready"] = latest if newer else None
    return newer


def check_update(current_version, force=False):
    """节流检查。返回 dict(latest/current/newer/error)；网络失败写进 error。

    成功路径会顺带更新 `UPDATE_READY`（新版号，或 None）——菜单可用性据此变化。
    error 里带配额人话（见 `http_error_hint`），调用方不必自己判 403。
    """
    if (not force and _STATE.get("checked_for") == current_version
            and time.time() - _STATE.get("at", 0) < CHECK_INTERVAL):
        latest = _STATE["latest"]
        return {"latest": latest, "current": current_version,
                "newer": _publish(latest, current_version), "error": ""}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": APP_ID},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = _tag_to_version(str(data.get("tag_name") or ""))
        if not latest:
            raise ValueError("release has no tag_name")
        _STATE.update(checked_for=current_version, latest=latest, at=time.time())
        return {"latest": latest, "current": current_version,
                "newer": _publish(latest, current_version), "error": ""}
    except Exception as exc:
        return {"latest": "", "current": current_version, "newer": False,
                "error": str(exc) + http_error_hint(exc)}


def _download(url, dest, timeout=120.0):
    req = urllib.request.Request(url, headers={"User-Agent": APP_ID})
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zip_sha256(zip_path, sha_text):
    """校验下载包，返回 `(ok, 人话)`。**期望值缺失也算失败**——绝不静默放行（reme 口径）。

    发布页少了 `.sha256` 时，包的真实性就没有任何独立依据；此时"跳过校验继续装"等于把
    一次 HTTPS 之外的完整性保障降级为零。宁可中止更新，也不静默放行。
    """
    wanted = str(sha_text or "").strip()
    if not wanted:
        return False, "校验更新包失败：发布页缺少 .sha256 校验文件，已中止更新（无法校验完整性）"
    wanted = wanted.split()[0].strip().lower()
    actual = _sha256(str(zip_path)).lower()
    if wanted != actual:
        return False, f"更新包校验失败：sha256 对不上（期望 {wanted[:12]}…，实际 {actual[:12]}…）"
    return True, ""


def build_apply_script(target_dir, stage_dir, work_dir, backup_dir, log_path,
                       limit=UPDATE_WAIT_LIMIT, snapshot_dir=None, failed_marker=None):
    """生成替换脚本文本（**纯函数**，便于回归断言其语义要点）。

    语义要点（缺一即回归，见 README）：镜像名等待旧进程退出且**不用管道**、等待上限、
    备份在 target 之外、**替换前先快照且成功才轮转**、**检查 rc 且失败回铺不启动**、
    失败写 marker、`/e /purge` 铺新、成功与放弃都走收尾、脚本自删。
    """
    work_dir = Path(work_dir)
    if snapshot_dir is None:
        snapshot_dir = work_dir.parent / "_backup.pre"
    if failed_marker is None:
        failed_marker = failed_marker_path(work_dir)
    return _APPLY_BAT.format(
        target=target_dir, stage=stage_dir, work=work_dir, backup=backup_dir,
        snapshot=snapshot_dir, failed=failed_marker, log=log_path,
        exe=EXE_NAME, newexe=os.path.join(str(target_dir), EXE_NAME),
        limit=limit, tick_ms=UPDATE_WAIT_TICK_MS,
        # 1.4.4：budget **必须与 limit 同源**。旧写法传模块常量 `UPDATE_WAIT_BUDGET_S`
        # （按 UPDATE_WAIT_LIMIT=120 算），调用方传 `limit=2` 时会渲染出
        # "2 polls x 1000ms (nominal budget 120s)"——一句不可能的话。
        budget_s=limit * UPDATE_WAIT_TICK_MS // 1000,
        stamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def failed_marker_path(update_dir):
    """失败 marker 的位置：用户在数据区一眼能看到，且与暂存目录分开（暂存会被删）。"""
    return Path(update_dir).parent / FAILED_MARKER_NAME


def pop_failed_update_note(update_dir, log=lambda *a: None):
    """读一次"上次更新失败"的 marker，返回人话（无 marker 则空串），并删除 marker。

    托盘已退出、更新器也自删了，**失败只能等下次启动说**——这是 reme 已验证的可见性机制。
    读取失败不抛（不该拦住启动）。

    **顺序：先算文案 → 先报告 → 最后才删证据**（2026-09-19 修，C-32）。旧写法把
    `marker.unlink()` 与 `read_text()` 放在同一个 try 里：`unlink` 抛 OSError（marker 被
    Defender/索引器短暂锁定——本仓库实测过）会走 except 直接 `return ""`，**detail 明明
    已经读到了，用户却看不到升级失败提示**；更糟的是**调用边界上的错误**（arity/签名不符、
    属性不存在）在进入被调方之前就抛出，`except OSError` 根本拦不住，证据已删、日志无声。
    删不掉不该惩罚读者：留给下次启动再报一次即可。
    """
    marker = failed_marker_path(update_dir)
    try:
        if not marker.is_file():
            return ""
        detail = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        log("failed-update marker read error:", exc)
        return ""
    log("previous update failed:", detail)
    try:
        marker.unlink()
    except OSError as exc:
        log("failed-update marker not removed (will report again):", exc)
    return ("上次自动更新失败，已回退到原版本并保留现场；详见 update.log"
            + (f"（{detail}）" if detail else ""))


def download_and_prepare(latest, target_dir, update_dir, log=lambda *a: None,
                         backup_dir=None, snapshot_dir=None):
    """下载 zip（sha256 校验）→ 解包暂存 → 生成退出时执行的一次性替换脚本。

    签名与 1.0.x 一致（`backup_dir` / `snapshot_dir` 是新增可选参数，默认 `update_dir`
    同级 `_backup` / `_backup.pre`，必须在 target **之外**——见脚本内注释）。zip 打包约定
    （release.yml）：压缩包里带一层 <APP_ID>-<版本>/ 目录。
    """
    update_dir = Path(update_dir)
    base = f"https://github.com/{REPO}/releases/download/v{latest}"
    stem = f"{APP_ID}-{latest}-windows-x64"
    zip_path = update_dir / (stem + ".zip")
    log("downloading", stem)
    _download(f"{base}/{stem}.zip", str(zip_path))
    sha_path = str(zip_path) + ".sha256"
    try:
        _download(f"{base}/{stem}.zip.sha256", sha_path)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "发布页缺少 %s.zip.sha256 校验文件，已中止更新（无法校验完整性）：%s" % (stem, exc)
        ) from exc
    ok, detail = verify_zip_sha256(zip_path, open(sha_path, "r", encoding="utf-8").read())
    if not ok:
        raise RuntimeError(detail)
    log("sha256 ok")
    with zipfile.ZipFile(str(zip_path)) as z:
        names = z.namelist()
        z.extractall(str(update_dir))
    staged = update_dir / stem
    if not (staged / EXE_NAME).is_file():
        if any(n == EXE_NAME or n.endswith("/" + EXE_NAME) for n in names):
            staged = update_dir  # 兜底：扁平 zip
        else:
            raise RuntimeError("staged exe missing after extract")
    if backup_dir is None:
        backup_dir = update_dir.parent / "_backup"
    log_path = update_dir.parent / "update.log"
    text = build_apply_script(target_dir, staged, update_dir, backup_dir, log_path,
                              snapshot_dir=snapshot_dir)
    # 脚本放 %TEMP%：不能放 STAGE 内（收尾删 STAGE 会锁住正在执行的它）
    script = Path(tempfile.gettempdir()) / f"{APP_ID}-update.bat"
    try:
        # cmd.exe 按机器 ANSI 代码页解析 .bat ⇒ 按 ANSI 落盘（路径里可能有中文）
        script.write_text(text, encoding="mbcs", errors="replace")
    except (LookupError, UnicodeError):
        script.write_text(text, encoding="utf-8")
    _PUBLISHED["pending_cmd"] = str(script)
    log("update staged:", str(staged), "->", str(target_dir), "(bat %s)" % script)
    return str(script)


def sweep_stale_update_dirs(max_age=3600.0):
    """清掉更新器遗留在 %TEMP% 的**暂存目录**与**替换脚本**，返回清掉的个数。

    正常路径由脚本收尾；但它可能被打断（重启/被杀/半路消失），那之后就
    没有任何东西知道那份**解压好的整包**在哪了（reme 实测每次约 50MB，四轮积 201MB）。
    只动本应用自己命名的那一类（`<APP_ID>-update*`），且只动 **max_age 之前**的：
    正在进行的更新，其产物是刚建的，绝不能碰。清扫失败不抛（不该拦住启动）。

    ⚠️ **两类都要清**（2026-09-19 缺陷，helpers-dev 找到）：暂存是**目录**，替换脚本是
    **文件**（`<APP_ID>-update.bat`）。旧实现同时错了两处——前缀带尾随 `-`（匹配不到
    `…-update.bat`）+ 只处理 `is_dir()`——于是**中断的更新会永久留下脚本文件**，
    而这个函数正是唯一应该清掉它的地方。
    """
    removed = 0
    try:
        cutoff = time.time() - max_age
        for path in Path(tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
                removed += 1
            except OSError:
                continue
    except Exception:
        return removed
    return removed
