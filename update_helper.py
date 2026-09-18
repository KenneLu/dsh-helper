# -*- coding: utf-8 -*-
# TEMPLATE-FROM: _template/modules/update_helper/update_helper.py | TEMPLATE-VER: 1.0.0
"""T4｜在线更新三段式：查（GitHub Releases）→ 下（zip + sha256）→ 换（退出后铺目录并重启）。

换目录为什么必须"独立脚本"：Windows 上运行中的 exe 换不掉。退出托盘时拉起
apply.cmd：等本进程消失 → robocopy /MIR 铺到目标目录 → 重启新 exe → 脚本自删。
有稳定安装位（paths.INSTALL_DIR）的工具以稳定位为 TARGET；没有的工具以运行目录
为 TARGET（local-speak2text / dsh-helper / opencodex-helper 三种实例都有）。
"""
import hashlib
import json
import os
import time
import urllib.request
import zipfile

from appconfig import APP_ID, EXE_NAME, REPO_NAME, REPO_OWNER

REPO = f"{REPO_OWNER}/{REPO_NAME}"
CHECK_INTERVAL = 24 * 3600
UPDATE_READY = None   # 有新版时的版本号；None=无（控制「下载并更新」菜单可用性）
PENDING_CMD = None    # 已就绪的一次性安装脚本路径；None=无（控制退出时是否拉起）

_STATE = {"checked_for": "", "latest": "", "at": 0.0}


def _tag_to_version(tag):
    return tag[1:] if tag.startswith("v") else tag


def _version_is_newer(latest, current):
    def nums(v):
        return [int(x) for x in v.split(".") if str(x).isdigit()]
    try:
        return nums(latest) > nums(current)
    except Exception:
        return False


def check_update(current_version, force=False):
    """节流检查。返回 dict(latest/current/newer/error)；网络失败写进 error。"""
    if (not force and _STATE.get("checked_for") == current_version
            and time.time() - _STATE.get("at", 0) < CHECK_INTERVAL):
        latest = _STATE["latest"]
        return {"latest": latest, "current": current_version,
                "newer": _version_is_newer(latest, current_version), "error": ""}
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
                "newer": _version_is_newer(latest, current_version), "error": ""}
    except Exception as exc:
        return {"latest": "", "current": current_version, "newer": False, "error": str(exc)}


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


def download_and_prepare(latest, target_dir, update_dir, log=lambda *a: None):
    """下载 zip（sha256 校验）→ 解包暂存 → 生成退出时执行的一次性安装脚本。

    zip 打包约定（release.yml）：压缩包里带一层 <APP_ID>-<版本>/ 目录。
    """
    global PENDING_CMD
    base = f"https://github.com/{REPO}/releases/download/v{latest}"
    stem = f"{APP_ID}-{latest}-windows-x64"
    zip_path = os.path.join(update_dir, stem + ".zip")
    log("downloading", stem)
    _download(f"{base}/{stem}.zip", zip_path)
    try:
        sha_path = zip_path + ".sha256"
        _download(f"{base}/{stem}.zip.sha256", sha_path)
        expected = open(sha_path, "r", encoding="utf-8").read().split()[0].lower()
        actual = _sha256(zip_path).lower()
        if expected != actual:
            raise RuntimeError(f"sha256 mismatch: {actual}")
        log("sha256 ok")
    except FileNotFoundError:
        log("no sha256 file, skip verify")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        z.extractall(update_dir)
    staged = os.path.join(update_dir, stem)
    if not os.path.exists(os.path.join(staged, EXE_NAME)):
        if any(n == EXE_NAME or n.endswith("/" + EXE_NAME) for n in names):
            staged = update_dir  # 兜底：扁平 zip
        else:
            raise RuntimeError("staged exe missing after extract")
    os.makedirs(update_dir, exist_ok=True)
    cmd_path = os.path.join(update_dir, "apply.cmd")
    script = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        "robocopy \"%STAGED%\" \"%TARGET%\" /MIR /R:1 /W:1 /NFL /NDL /NP >nul\r\n"
        "start \"\" \"%NEWEXE%\"\r\n"
        "del \"%~f0\"\r\n"
    ).replace("%STAGED%", staged).replace("%TARGET%", str(target_dir)).replace(
        "%NEWEXE%", os.path.join(str(target_dir), EXE_NAME))
    with open(cmd_path, "w", encoding="ascii") as f:
        f.write(script)
    PENDING_CMD = cmd_path
    return cmd_path
