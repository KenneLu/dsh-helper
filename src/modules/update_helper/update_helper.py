# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/update_helper/update_helper.py | TEMPLATE-VER: 1.2.0
"""T4｜在线更新三段式：查（GitHub Releases）→ 下（zip + sha256）→ 换（退出后铺目录并重启）。

1.2.0（2026-09-19）：**语义照 reme-helper 已验证的更新链重写**（仲裁规则：公共件以
reme 的已验证实现为基准，不看"哪个版本用的人多"）。1.0.x 是**合成件**——从未被端到端
验证过，两处静默失效即由此而来（`PENDING_CMD` 被包 `import *` 复制、`UPDATE_READY`
从不赋值）。本版把 reme 实测过的语义要点搬进来，**调用签名保持不变**：

  * 替换脚本**以镜像名等待旧进程真正退出**（`tasklist` 写文件 + `find` 读文件，**刻意
    不用管道**——DETACHED 无 console 时 `tasklist | find` 永不返回，实测 0.13s vs 永久
    挂起），替代原先写死的 `timeout /t 2`；等待有**上限**，超时走 `:giveup` 记日志而非
    无限等；
  * 铺目录前**先备份**到目标目录**之外**（否则 `robocopy /purge` 会把备份一起删、且
    `robocopy target target\\_backup /e` 会扫到自己的输出）；
  * 用 `robocopy /e /purge` 清掉上一版**残留文件**；
  * 全流程写 `update.log`；`:cleanup` **成功与放弃两条路径都走**，负责删暂存（reme 实测
    每次 ~50MB，不收会积在 %TEMP%）并自删脚本；
  * 新增 `sweep_stale_update_dirs()`：清扫被中断的更新遗留在 %TEMP% 的整包，
    **只清一小时前的**（保护进行中的更新）；
  * 新增 `http_error_hint()`：403/429 是 GitHub **匿名配额**（每出口 IP 每小时 60 次，
    与同网其他工具共享），翻成人话而不是裸 `HTTP Error 403`。

包门面 `__init__.py` 不复制状态（见其 docstring）；状态经 `update_ready()` /
`pending_cmd()` 读取。
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from modules.appconfig import APP_ID, EXE_NAME, REPO_NAME, REPO_OWNER

REPO = f"{REPO_OWNER}/{REPO_NAME}"
CHECK_INTERVAL = 24 * 3600
UPDATE_READY = None   # 有新版时的版本号；None=无（控制「下载并更新」菜单可用性）
PENDING_CMD = None    # 已就绪的一次性安装脚本路径；None=无（控制退出时是否拉起）

# 等旧进程退出的上限：120 次 × 约 1 秒（`ping -n 2` 的节奏）——reme 实测值
UPDATE_WAIT_LIMIT = 120
# %TEMP% 下暂存目录/脚本的识别前缀
TEMP_PREFIX = f"{APP_ID}-update-"

_STATE = {"checked_for": "", "latest": "", "at": 0.0}

# 替换脚本模板。**ASCII-only**（cmd.exe 按机器 ANSI 代码页解析；解释一律留在 Python 侧）。
# 刻意**不用括号块**：cmd 对块内 errorlevel 的解析不可靠，全程 goto（reme 实测结论）。
_APPLY_BAT = r"""@echo off
setlocal
set "STAGE={stage}"
set "TARGET={target}"
set "BACKUP={backup}"
set "LOG={log}"
set "POLL=%LOG%.poll"
echo [{stamp}] start target=%TARGET% backup=%BACKUP% >> "%LOG%"
set /a tries=0
:wait
rem NO PIPE HERE, on purpose: this script is spawned with DETACHED_PROCESS and has
rem no console; in that context "tasklist | find" NEVER RETURNS (find blocks on
rem stdin forever) and the update silently never happens. tasklist writes to a file
rem and find reads that file instead. (reme-helper: measured 0.13s vs hang.)
tasklist /fi "imagename eq {exe}" /nh > "%POLL%" 2>nul
find /i "{exe}" "%POLL%" >nul
if errorlevel 1 goto gone
set /a tries+=1
if %tries% geq {limit} goto giveup
ping -n 2 127.0.0.1 >nul
goto wait
:gone
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
rem Backup lives OUTSIDE the target: the /purge below would delete it otherwise.
robocopy "%TARGET%" "%BACKUP%" /e /njh /njs /nfl /ndl >nul
rem /purge removes files the previous version left behind.
robocopy "%STAGE%" "%TARGET%" /e /purge /njh /njs /nfl /ndl >> "%LOG%" 2>&1
echo [{stamp}] copied rc=%ERRORLEVEL% >> "%LOG%"
start "" "{newexe}"
goto cleanup
:giveup
echo [{stamp}] aborted: {exe} still running after {limit}s >> "%LOG%"
:cleanup
rem Runs on BOTH paths: without it the staged package (~50MB/update) stays behind
rem forever. The script itself lives in %TEMP% (NOT in STAGE), so removing STAGE
rem cannot lock the running file.
if exist "{work}" rmdir /s /q "{work}"
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
    """有新版时的版本号，否则 None（**推荐读法**）。

    状态经访问器暴露，外部不要直接读可变全局——包门面若 `import *`，那份是静态副本。
    """
    return UPDATE_READY


def pending_cmd():
    """已就绪的一次性安装脚本路径，否则 None（**推荐读法**）。"""
    return PENDING_CMD


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
    global UPDATE_READY
    newer = bool(latest) and _version_is_newer(latest, current_version)
    UPDATE_READY = latest if newer else None
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


def build_apply_script(target_dir, stage_dir, work_dir, backup_dir, log_path,
                       limit=UPDATE_WAIT_LIMIT):
    """生成替换脚本文本（**纯函数**，便于回归断言其语义要点）。

    语义要点（缺一即回归，见 README）：镜像名等待旧进程退出且**不用管道**、等待上限、
    备份在 target 之外、`/e /purge` 铺新、成功与放弃都走 `:cleanup`、脚本自删。
    """
    return _APPLY_BAT.format(
        stage=stage_dir, target=target_dir, backup=backup_dir, log=log_path,
        exe=EXE_NAME, newexe=os.path.join(str(target_dir), EXE_NAME),
        work=work_dir, limit=limit,
        stamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def download_and_prepare(latest, target_dir, update_dir, log=lambda *a: None,
                         backup_dir=None):
    """下载 zip（sha256 校验）→ 解包暂存 → 生成退出时执行的一次性替换脚本。

    签名与 1.0.x 一致（`backup_dir` 是新增可选参数，默认 `update_dir` 同级 `_backup`，
    必须在 target **之外**——见脚本内注释）。zip 打包约定（release.yml）：压缩包里带
    一层 <APP_ID>-<版本>/ 目录。
    """
    global PENDING_CMD
    update_dir = Path(update_dir)
    base = f"https://github.com/{REPO}/releases/download/v{latest}"
    stem = f"{APP_ID}-{latest}-windows-x64"
    zip_path = update_dir / (stem + ".zip")
    log("downloading", stem)
    _download(f"{base}/{stem}.zip", str(zip_path))
    try:
        sha_path = str(zip_path) + ".sha256"
        _download(f"{base}/{stem}.zip.sha256", sha_path)
        expected = open(sha_path, "r", encoding="utf-8").read().split()[0].lower()
        actual = _sha256(str(zip_path)).lower()
        if expected != actual:
            raise RuntimeError(f"sha256 mismatch: {actual}")
        log("sha256 ok")
    except FileNotFoundError:
        log("no sha256 file, skip verify")
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
    text = build_apply_script(target_dir, staged, update_dir, backup_dir, log_path)
    # 脚本放 %TEMP%：不能放 STAGE 内（`:cleanup` 删 STAGE 会锁住正在执行的它）
    script = Path(tempfile.gettempdir()) / f"{APP_ID}-update.bat"
    try:
        # cmd.exe 按机器 ANSI 代码页解析 .bat ⇒ 按 ANSI 落盘（路径里可能有中文）
        script.write_text(text, encoding="mbcs", errors="replace")
    except (LookupError, UnicodeError):
        script.write_text(text, encoding="utf-8")
    PENDING_CMD = str(script)
    log("update staged:", str(staged), "->", str(target_dir), "(bat %s)" % script)
    return str(script)


def sweep_stale_update_dirs(max_age=3600.0):
    """清掉更新器遗留在 %TEMP% 的暂存目录，返回清掉的个数。

    正常路径由脚本 `:cleanup` 收尾；但它可能被打断（重启/被杀/半路消失），那之后就
    没有任何东西知道那份**解压好的整包**在哪了（reme 实测每次约 50MB，四轮积 201MB）。
    只动本应用自己命名的那一类（`<APP_ID>-update-*`），且只动 **max_age 之前**的：
    正在进行的更新，其暂存目录是刚建的，绝不能碰。清扫失败不抛（不该拦住启动）。
    """
    removed = 0
    try:
        cutoff = time.time() - max_age
        for path in Path(tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
            try:
                if path.is_dir() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except Exception:
        return removed
    return removed
