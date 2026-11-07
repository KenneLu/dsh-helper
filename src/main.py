#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-helper：用 Windows 托盘菜单管理 DeepSeek Harness Web UI。"""

import json
import functools
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, simpledialog
from html import escape as html_escape
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import psutil
import pystray
from PIL import Image, ImageDraw, ImageOps

# 容错：数据根/数据区环境变量被设成**纯空白**（如 " "）时视为未设置。否则
# paths 会把 USER_DATA_DIR 算成 " \dsh-helper" 这种空格目录，启动即崩（实测：
# cmd 的 `set "VAR= "` 会留下一个空格；正常 unset 才是真空）。必须在 import
# template.paths **之前**清洗——USER_DATA_DIR 在模块导入时就算死了。
for _env_name in ("DSH_HELPER_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME"):
    if _env_name in os.environ and not os.environ[_env_name].strip():
        del os.environ[_env_name]

from template import autostart, i18n, log_kit, paths, tray_icons, tray_kit, update_helper, update_ui   # noqa: E402
from template.appconfig import APP_ID, ICON_ASSET as ICON_ASSET_REL   # noqa: E402


APP_NAME = "dsh-helper"
VERSION = "1.8.13"
# 程序本体目录**不在本文件派生**：唯一出处是 paths 的 APP_DIR（打包后 = exe 所在
# 目录，开发态 = 仓库根）。
# 用户数据区/配置/日志/更新暂存同样出自 paths（数据区住 LOCALAPPDATA，
# 1.6 及以前的 exe 旁旧配置由播种自动迁入）。
from template.paths import (APP_DIR, CONFIG_PATH, LEGACY_CONFIG_PATH, LOG_DIR, LOG_PATH,
                           UPDATE_DIR, USER_DATA_DIR)
try:
    import ctypes
except ImportError:  # pragma: no cover - Windows 一定有；非 Windows 源码态降级为无弹窗
    ctypes = None
SHOW_REQUEST_PATH = USER_DATA_DIR / "show.request"   # 第二实例请求常驻实例提示"已在运行"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost):([0-9]{1,5})(?:/[^\s]*)?", re.I)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep DSH's token-exchange 3xx response visible to the readiness probe."""

    def http_error_301(self, req, fp, code, msg, headers):
        return fp

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    def http_error_303(self, req, fp, code, msg, headers):
        return fp

    def http_error_307(self, req, fp, code, msg, headers):
        return fp

    def http_error_308(self, req, fp, code, msg, headers):
        return fp


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def resource_path(name):
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", APP_DIR)) / name
        if bundled.exists():
            return bundled
    return APP_DIR / name


# 资源相对路径的**唯一来源是 appconfig.ICON_ASSET**（icons.py 构建期读的也是那一处）。
# 这里只做"解析"，不再重抄一遍字面量：抄两份的后果是改了 appconfig 里那个名字时，
# 构建期的 ico 跟着变、运行时的托盘图还按老名字找，两边静默分叉。
# （同族问题见上面 APP_DIR：同名派生量只许有一个来源。）
ICON_ASSET = resource_path(ICON_ASSET_REL) if ICON_ASSET_REL else None
_ICON_BASE = None

STATUS_REFRESH_INTERVAL_CHOICES = (
    (5, "interval_5s"),
    (20, "interval_20s"),
    (60, "interval_1m"),
    (300, "interval_5m"),
    (600, "interval_10m"),
)
STATUS_REFRESH_INTERVAL_VALUES = {seconds for seconds, _label in STATUS_REFRESH_INTERVAL_CHOICES}
DEFAULT_STATUS_REFRESH_INTERVAL_SEC = 60
VALIDATION_FAILURE_NOTIFY_DELAY_SEC = 2.0
# `dsh.cmd web --help` 的超时。dsh.cmd 是 Node CLI，每次调用都要冷启动 Node + 加载插件。
# 本机 8 次连测（2026-09-19）：9.2 / 9.8 / 9.8 / 10.6 / 10.8 / 10.9 / 10.9 / 11.7 秒
# （中位 ~10.7s，最坏 11.7s）。原先写 10s，正好落在分布中段 —— 冻结冒烟于是随机红/绿，
# 而报错只说"校验失败"，看起来像环境坏了，而不是超时太紧。
# 取 45s：对最坏实测有 3.9 倍余量（家族口径 ≥3 倍），也覆盖**冷启动**（重启后首次调用
# 没有 OS 文件缓存，会比上表慢，而这是热机状态下量不到的那条分支）。
# 宁可多等（校验跑在后台线程 + "正在校验…"通知），也不要一个会自己抖的门禁。
DSH_CMD_VALIDATE_TIMEOUT_SEC = 45
# 默认固定端口：与 dsh web 官方兜底端口（3080）一致；该端口被占用时本次回退为自动端口。
DEFAULT_PORT = 3080

# ---- dsh 升级助手 -----------------------------------------------------------
# 报告/记录住**用户数据区**（不进安装目录：OTA 更新会覆盖安装目录，而升级记录要
# 长期留存、供多次升级对比）。profile 默认 web——本工具管理的一直是 dsh web。
DSH_UPGRADE_REPORT_DIR = USER_DATA_DIR / "upgrade" / "reports"
DSH_UPGRADE_RECORD_DIR = USER_DATA_DIR / "upgrade" / "records"
DSH_UPGRADE_PROFILE = "web"
# `dsh --version` 是 Node CLI，冷启动慢；仅报告里的「运行中版本」用，给足超时。
DSH_VERSION_TIMEOUT_SEC = 20

DEFAULT_CONFIG = {
    "dsh_cmd": "",
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    "status_refresh_interval_sec": DEFAULT_STATUS_REFRESH_INTERVAL_SEC,
    "start_on_launch": False,
    # 自启状态不落 config（数据·单一真源 = HKCU Run）：注册表是唯一出处，
    # 托盘勾选直接读注册表，见 template/autostart。
    # G4.2 条款 5：退出清理勾选，持久化、默认不勾——不勾 = dsh 服务放行继续运行
    "quit_stop_dsh": False,
}

# 日志初始化失败的原因（None = 正常）。启动失败提示会据此补充"日志目录也不可写"。
LOG_INIT_ERROR = None


class _NoopLogger:
    """日志不可用时的兜底：吃掉所有日志调用，绝不让日志把主流程弄死。"""

    def info(self, *_parts) -> None:
        return None


try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _logger = log_kit.get_logger(LOG_DIR)   # 滚动 1MB×3（家族标准）
    # log_kit 1.0.5 起初始化失败在模板层静默（NullHandler + LAST_INIT_ERROR）；
    # 工具侧仍要保留「可见的失败信号 + LOG_INIT_ERROR」诊断契约，故把它转成异常走同一兜底。
    if getattr(log_kit, "LAST_INIT_ERROR", ""):
        raise RuntimeError(log_kit.LAST_INIT_ERROR)
except Exception as exc:  # noqa: BLE001 - 日志永远不能把主流程弄死
    LOG_INIT_ERROR = exc
    try:
        print("log init failed: %s" % exc, file=sys.stderr, flush=True)
    except Exception:
        pass
    _logger = _NoopLogger()


def log(*parts):
    """模板件的日志契约是 **print 形态**（`log("下载中", name)`），与 log_kit 的单参闭包不同。

    `template/update_helper` 里有 6 处多参调用；传单参的 log 进去，它们会在**真路径**上
    TypeError —— 而命中的正是"每次下载"(L407)、"每次成功拉起替换脚本"(L257)、
    "存在失败 marker 时"(L390) 这类必然会走到的行。dsh 的更新链因此从来没跑通过。
    这里按契约收任意个参数再拼接（模板 log_kit 待 tpl-keeper 统一为同一形态）。
    """
    _logger.info(" ".join(str(part) for part in parts))


def load_config():
    paths.seed_config()   # exe 旁旧配置一次性迁入用户数据区
    config = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            log(f"config load failed: {exc}")
    merged = {**DEFAULT_CONFIG, **config}
    try:
        merged["port"] = int(merged.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        merged["port"] = DEFAULT_PORT
    if merged["port"] < 0 or merged["port"] > 65535:
        merged["port"] = DEFAULT_PORT
    merged["host"] = str(merged.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    merged["dsh_cmd"] = str(merged.get("dsh_cmd") or "").strip()
    try:
        merged["status_refresh_interval_sec"] = int(
            merged.get("status_refresh_interval_sec", DEFAULT_STATUS_REFRESH_INTERVAL_SEC)
        )
    except (TypeError, ValueError):
        merged["status_refresh_interval_sec"] = DEFAULT_STATUS_REFRESH_INTERVAL_SEC
    if merged["status_refresh_interval_sec"] not in STATUS_REFRESH_INTERVAL_VALUES:
        merged["status_refresh_interval_sec"] = DEFAULT_STATUS_REFRESH_INTERVAL_SEC
    merged["start_on_launch"] = bool(merged.get("start_on_launch", False))
    # 旧版遗留的 "autostart" 键不再读取（真源=注册表）；不主动删除以免破坏用户文件，
    # 下次 save_config 落盘时自然消失（数据·单一真源）。
    return merged


CFG = load_config()
# 语言在配置读取之后、任何 t() 之前初始化（auto 跟随 Windows UI 语言）。
i18n.init(i18n.load_language_from_config(CONFIG_PATH))


def save_config():
    try:
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "dsh_cmd": CFG.get("dsh_cmd", ""),
                    "host": CFG.get("host", "127.0.0.1"),
                    "port": CFG.get("port", DEFAULT_PORT),
                    "status_refresh_interval_sec": current_status_refresh_interval(),
                    "start_on_launch": bool(CFG.get("start_on_launch", False)),
                    # G4.2 条款 5：退出清理勾选必须随 save_config 落盘——
                    # on_change 即勾即存走的就是这里，漏键 = 持久化静默失效。
                    "quit_stop_dsh": bool(CFG.get("quit_stop_dsh", False)),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="mbcs" if os.name == "nt" else "utf-8",
        )
    except Exception as exc:
        log(f"config save failed: {exc}")


def current_status_refresh_interval():
    value = CFG.get("status_refresh_interval_sec", DEFAULT_STATUS_REFRESH_INTERVAL_SEC)
    return value if value in STATUS_REFRESH_INTERVAL_VALUES else DEFAULT_STATUS_REFRESH_INTERVAL_SEC


STATE_LOCK = threading.RLock()
ACTION_LOCK = threading.Lock()
COMMAND_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
STATE = {
    "phase": "stopped",  # stopped / starting / running / stopping / error
    "command_status": "checking",  # checking / ready / missing / invalid
    "url": "",
    "pid": None,
    "managed": False,
    "message": "",
    "last_output": [],
}
MANAGED_PROCESS = None
TRAY_ICON = None


def set_state(**values):
    with STATE_LOCK:
        STATE.update(values)


def state_copy():
    with STATE_LOCK:
        return dict(STATE)


def initial_tray_state():
    """创建托盘时的初态：必须来自**当前业务状态**，不得硬编码 stopped（1.8.8 修复）。

    启动顺序是 refresh_state() → 建托盘。若 dsh Web 已在运行（接管路径），
    refresh_state() 会把 phase 置为 running，并把该签名记进 MENU_SIG；等托盘建好后
    状态不再变化，rebuild_menu() 就不会再跑——此时若初态写死 stopped，图标会永久
    停在灰色 stopped 图，而菜单文字（lambda 实时求值）仍显示「运行中」。
    实测复现：1.8.7 + 「dsh Web 已在运行→重启 helper」。
    """
    return "running" if state_copy().get("phase") == "running" else "stopped"


def configured_dsh_command_path():
    value = str(CFG.get("dsh_cmd", "") or "").strip()
    return Path(os.path.expandvars(os.path.expanduser(value))) if value else None


def configured_dsh_command_file_exists():
    candidate = configured_dsh_command_path()
    if candidate is None or candidate.name.casefold() != "dsh.cmd":
        return False
    try:
        return candidate.is_file()
    except OSError:
        return False


def command_needs_startup_detection():
    return not configured_dsh_command_file_exists()


def resolve_dsh_command():
    """只解析已写入配置的 dsh.cmd，不隐式回退到硬编码路径。"""
    candidate = configured_dsh_command_path()
    if candidate is None or candidate.name.casefold() != "dsh.cmd":
        return None
    try:
        return str(candidate) if candidate.is_file() else None
    except OSError:
        return None


def discover_dsh_command_candidates():
    """按配置、PATH 和 npm 常见目录收集 dsh.cmd 候选路径。"""
    raw_candidates = [CFG.get("dsh_cmd", "")]

    try:
        result = subprocess.run(
            ["where.exe", "dsh.cmd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="mbcs" if os.name == "nt" else "utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        if result.returncode == 0:
            raw_candidates.extend(result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"where dsh.cmd failed: {exc}")

    raw_candidates.append(shutil.which("dsh.cmd"))

    npm_prefix = os.environ.get("NPM_CONFIG_PREFIX", "").strip()
    if npm_prefix:
        raw_candidates.append(Path(npm_prefix) / "dsh.cmd")
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        raw_candidates.append(Path(appdata) / "npm" / "dsh.cmd")

    candidates = []
    seen = set()
    for raw in raw_candidates:
        if not raw:
            continue
        candidate = Path(os.path.expandvars(os.path.expanduser(str(raw).strip().strip('"'))))
        if candidate.name.casefold() != "dsh.cmd":
            continue
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


# ---- dsh 升级助手：semver 范围匹配 ------------------------------------------
# dsh 插件用 npm semver 声明宿主版本范围（engines.dsh / peerDependencies）。判据必须
# 与 dsh 运行时一致（dsh-app-boot 的 unmetRequirements）：
#   · engines.dsh 存在时**独自决定**，peerDependencies 忽略；
#   · 否则每个 @deepseek-ai/dsh 或 @deepseek-ai/dsh-* peer 都**必须**满足（不是任一）；
#   · workspace:^ / ~ / * peer 指当前宿主，恒满足；
#   · includePrerelease：prerelease 版本参与范围匹配（0.2.2-rc.1 要能命中 >=0.2.0）。
# 这里实现 npm semver 的子集（^ ~ >= > < <= = 裸版本 + 空格 AND + || OR），不引第三方
# 依赖：打包态不自带 packaging/semver，自写可避免「开发机能跑、冻结包判错」。
_SEMVER_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_RANGE_VERSION_RE = re.compile(
    r"^v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_RANGE_OPS = (">=", "<=", ">", "<", "=", "^", "~")
_WORKSPACE_PEERS = ("workspace:^", "workspace:~", "workspace:*")
# npm 范围上界的 `-0`：单个数字标识符 "0" 的编码（数字标识符 < 非数字）。
_PRERELEASE_ZERO = ((0, 0, ""),)


def _prerelease_key(text):
    """prerelease 标识符元组；数字标识符 < 非数字标识符（semver 优先级规则）。"""
    if not text:
        return ()
    parts = []
    for ident in str(text).split("."):
        if ident == "":
            return None
        if ident.isdigit():
            parts.append((0, int(ident), ""))
        else:
            parts.append((1, 0, ident))
    return tuple(parts)


def _parse_semver(text):
    """解析具体版本；返回 (major, minor, patch, prerelease) 或 None。"""
    if not text:
        return None
    match = _SEMVER_RE.match(str(text).strip())
    if not match:
        return None
    pre = _prerelease_key(match.group(4)) if match.group(4) else ()
    if pre is None:
        return None
    return (int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0), pre)


def _parse_range_version(text):
    """解析范围里的版本 token；返回 (major, minor, patch, pre, specified) 或 None。

    ``specified`` = 明确写出的数字段数（1..3），供 `~` 判断上界用；
    通配符段（x/X/*）视为未指定、值按 0 处理。
    """
    if not text:
        return None
    match = _RANGE_VERSION_RE.match(str(text).strip())
    if not match:
        return None
    vals = []
    specified = 0
    for raw in match.groups()[:3]:
        if raw is None or raw in ("x", "X", "*"):
            vals.append(0)
        else:
            vals.append(int(raw))
            specified += 1
    if specified == 0:
        return None
    pre = _prerelease_key(match.group(4)) if match.group(4) else ()
    if pre is None:
        return None
    return (vals[0], vals[1], vals[2], pre, specified)


def _semver_cmp(left, right):
    """semver 优先级比较；返回 -1/0/1。release > 同版本号的 prerelease。"""
    if left[:3] != right[:3]:
        return -1 if left[:3] < right[:3] else 1
    if left[3] == right[3]:
        return 0
    if not left[3]:
        return 1
    if not right[3]:
        return -1
    return -1 if left[3] < right[3] else 1


def _caret_bounds(target):
    """^ 的上下界；上界带 `-0`，与 npm 一致（^0.2.2-rc.1 = >=0.2.2-rc.1 <0.3.0-0）。"""
    major, minor, patch, pre, specified = target
    lower = (major, minor, patch, pre)
    if major > 0:
        upper = (major + 1, 0, 0, _PRERELEASE_ZERO)
    elif minor > 0:
        upper = (0, minor + 1, 0, _PRERELEASE_ZERO)
    elif specified >= 3:
        upper = (0, 0, patch + 1, _PRERELEASE_ZERO)
    elif specified == 2:
        upper = (0, 1, 0, _PRERELEASE_ZERO)
    else:
        upper = (1, 0, 0, _PRERELEASE_ZERO)
    return lower, upper


def _tilde_bounds(target):
    """~ 的上下界；~1.2.3 = >=1.2.3 <1.3.0-0，~1 = >=1.0.0 <2.0.0-0。"""
    major, minor, patch, pre, specified = target
    lower = (major, minor, patch, pre)
    if specified == 1:
        upper = (major + 1, 0, 0, _PRERELEASE_ZERO)
    else:
        upper = (major, minor + 1, 0, _PRERELEASE_ZERO)
    return lower, upper


def _match_range_token(version, token):
    """单个比较 token 是否命中（含操作符展开）。"""
    token = token.strip()
    if not token or token in ("*", "x", "X"):
        return True
    op = "="
    rhs = token
    for candidate in _RANGE_OPS:
        if token.startswith(candidate):
            op = candidate
            rhs = token[len(candidate):].strip()
            break
    if rhs in ("*", "x", "X"):
        return True
    target = _parse_range_version(rhs)
    if target is None:
        return False
    if op == "^":
        lower, upper = _caret_bounds(target)
        return _semver_cmp(version, lower) >= 0 and _semver_cmp(version, upper) < 0
    if op == "~":
        lower, upper = _tilde_bounds(target)
        return _semver_cmp(version, lower) >= 0 and _semver_cmp(version, upper) < 0
    result = _semver_cmp(version, (target[0], target[1], target[2], target[3]))
    if op == "=":
        return result == 0
    if op == ">=":
        return result >= 0
    if op == "<=":
        return result <= 0
    if op == ">":
        return result > 0
    if op == "<":
        return result < 0
    return False


def version_in_range(version, range_text):
    """npm semver 范围匹配（includePrerelease）。`||` 任一命中、组内 AND 全命中。"""
    parsed = _parse_semver(version)
    if parsed is None:
        return False
    text = str(range_text or "").strip()
    if not text:
        return False
    for group in text.split("||"):
        tokens = group.split()
        if tokens and all(_match_range_token(parsed, token) for token in tokens):
            return True
    return False


# ---- dsh 升级助手：安装版本读取 ---------------------------------------------
# 主读全局包清单（快、确定，不起 Node）；`dsh --version` 只用于报告里的运行态校验。
_DSH_VERSION_CACHE = {"key": None, "done": False, "state": "missing", "version": None}


def _read_dsh_version_state(command):
    """读 dsh 安装版本；返回 (state, version)。state ∈ ok / missing / error。"""
    if not command:
        return "missing", None
    prefix = Path(command).parent
    candidates = (
        prefix / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",
        prefix.parent / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",
    )
    found = False
    for pkg in candidates:
        try:
            if not pkg.is_file():
                continue
            found = True
            data = json.loads(pkg.read_text(encoding="utf-8"))
            version = str(data.get("version") or "").strip()
            return ("ok", version) if version else ("error", None)
        except (OSError, ValueError):
            continue
    return ("error", None) if found else ("missing", None)


def _dsh_version_cached():
    """带缓存的读取；refresh_state() 会令缓存失效（升级后 60s 内菜单自动更新）。"""
    key = resolve_dsh_command()
    if not _DSH_VERSION_CACHE["done"] or _DSH_VERSION_CACHE["key"] != key:
        _DSH_VERSION_CACHE["key"] = key
        _DSH_VERSION_CACHE["done"] = True
        _DSH_VERSION_CACHE["state"], _DSH_VERSION_CACHE["version"] = _read_dsh_version_state(key)
    return _DSH_VERSION_CACHE["state"], _DSH_VERSION_CACHE["version"]


def dsh_install_version():
    """当前全局安装的 dsh 版本；读不到返回 None。"""
    return _dsh_version_cached()[1]


def dsh_version_line():
    """菜单版本行：已安装显示版本，未安装/读取失败分别给结论（对齐 reme-helper）。"""
    state, version = _dsh_version_cached()
    if state == "ok":
        return i18n.t("menu_dsh_version", version)
    if state == "error":
        return i18n.t("menu_dsh_version", i18n.t("menu_dsh_version_error"))
    return i18n.t("menu_dsh_version", i18n.t("menu_dsh_version_missing"))


def dsh_cli_version(timeout=DSH_VERSION_TIMEOUT_SEC):
    """运行 `dsh --version` 取版本（Node 冷启动慢，仅报告里用）；失败返回 None。"""
    command = resolve_dsh_command()
    if not command:
        return None
    try:
        result = subprocess.run(
            [command, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            cwd=str(Path(command).parent),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"dsh --version failed: {exc}")
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def port_is_available(host, port):
    """指定端口当前是否可绑定；不设 SO_REUSEADDR，避免 Windows 把已监听端口误判为可用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((str(host), int(port)))
        return True
    except OSError:
        return False


def pick_free_port():
    """返回本次启动实际使用的端口：配置端口可用时用它，否则由系统随机分配。"""
    host = CFG.get("host", "127.0.0.1")
    requested = int(CFG.get("port", DEFAULT_PORT) or 0)
    if requested and port_is_available(host, requested):
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def resolve_launch_port():
    """返回 (port_arg, note)：配置端口可用则固定使用；被占用时回退为 0（由 dsh/系统选空闲端口）。"""
    host = str(CFG.get("host", "127.0.0.1"))
    requested = int(CFG.get("port", DEFAULT_PORT) or 0)
    if not requested:
        return "0", ""
    if port_is_available(host, requested):
        return str(requested), ""
    note = i18n.t("err_port_busy_note", requested)
    log(f"configured port {requested} unavailable on {host}: fallback to auto port")
    return "0", note


# 最近一次启动的端口回退说明（供状态消息与通知使用）。
LAUNCH_PORT_NOTE = ""


def normalize_url(url):
    match = URL_RE.search(url or "")
    if not match:
        return ""
    # DSH 0.1.2+ puts a one-time launch token in the query string. Keep the
    # complete URL so the browser can exchange it for the authenticated cookie.
    return match.group(0).rstrip(".,);]")


def url_port(url):
    match = URL_RE.search(url or "")
    return int(match.group(1)) if match else None


def probe_url(url):
    if not url:
        return False
    try:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        # A valid DSH token request returns a 303 and sets the browser-session
        # cookie before redirecting to the clean root URL. Do not follow that
        # redirect here: the helper has no browser cookie jar, but the 3xx is
        # already proof that the Web server and token are live.
        with NO_REDIRECT_OPENER.open(request, timeout=1.2) as response:
            return 200 <= int(response.getcode() or 0) < 400
    except urllib.error.HTTPError as exc:
        return 200 <= int(exc.code or 0) < 400
    except (OSError, urllib.error.URLError, ValueError):
        return False


def process_cmdline(proc):
    try:
        return " ".join(proc.info.get("cmdline") or [])
    except Exception:
        return ""


def is_dsh_web_process(proc):
    cmdline = process_cmdline(proc).lower()
    if not cmdline or "dsh" not in cmdline or "web" not in cmdline:
        return False
    return (
        "@deepseek-ai" in cmdline
        or "dsh.cmd" in cmdline
        or "dsh.ps1" in cmdline
        or "\\dsh\\lib\\bin.js" in cmdline
        or "/dsh/lib/bin.js" in cmdline
    )


def existing_dsh_processes():
    processes = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if is_dsh_web_process(proc):
                processes.append(proc)
    except Exception as exc:
        log(f"process scan failed: {exc}")
    return processes


def listener_ports_by_pid(pids):
    found = {}
    if not pids:
        return found
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.pid not in pids or conn.status != psutil.CONN_LISTEN:
                continue
            address = conn.laddr
            port = getattr(address, "port", None)
            host = getattr(address, "ip", "")
            if port and host in ("127.0.0.1", "0.0.0.0", "::1", "::"):
                found.setdefault(conn.pid, []).append(int(port))
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
        log(f"listener scan failed: {exc}")
    return found


def listener_pids_for_port(port):
    """返回占用指定本机端口的监听 PID。"""
    if not port:
        return set()
    result = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.pid:
                continue
            address = conn.laddr
            host = getattr(address, "ip", "")
            current_port = getattr(address, "port", None)
            if current_port == int(port) and host in ("127.0.0.1", "0.0.0.0", "::1", "::"):
                result.add(int(conn.pid))
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
        log(f"listener pid scan failed port={port}: {exc}")
    return result


def commandline_port(cmdline):
    match = re.search(r"(?:--port\s+|--port=)(\d+)", cmdline or "", re.I)
    return int(match.group(1)) if match else None


def discover_existing_dsh():
    """找到已经运行的 dsh web，并返回 (pid, url, commandline)。"""
    processes = existing_dsh_processes()
    ports = listener_ports_by_pid({p.pid for p in processes})
    for proc in processes:
        cmdline = process_cmdline(proc)
        candidates = sorted(set(ports.get(proc.pid, [])))
        if not candidates:
            port = commandline_port(cmdline)
            if port:
                candidates = [port]
        for port in candidates:
            url = f"http://127.0.0.1:{port}/"
            if probe_url(url):
                return proc.pid, url, cmdline
    return None


def append_output(line):
    line = line.rstrip()
    if not line:
        return
    log(f"dsh: {line}")
    with STATE_LOCK:
        STATE["last_output"] = (STATE.get("last_output", []) + [line])[-20:]
        url = normalize_url(line)
        if url:
            STATE["url"] = url


def read_process_output(proc):
    try:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            append_output(line)
    except Exception as exc:
        log(f"dsh output reader failed: {exc}")


class DshCommandError(RuntimeError):
    def __init__(self, detail, status):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def build_dsh_command():
    global LAUNCH_PORT_NOTE
    candidate = configured_dsh_command_path()
    if candidate is None:
        raise DshCommandError(i18n.t("err_cmd_not_set"), "missing")
    if candidate.name.casefold() != "dsh.cmd":
        raise DshCommandError(i18n.t("err_cmd_name"), "invalid")
    try:
        if not candidate.is_file():
            raise DshCommandError(i18n.t("err_cmd_not_exist"), "invalid")
    except OSError as exc:
        raise DshCommandError(i18n.t("err_cmd_unreadable", exc), "invalid") from exc

    command = str(candidate)
    host = str(CFG.get("host", "127.0.0.1"))
    port, LAUNCH_PORT_NOTE = resolve_launch_port()
    # 直接调用 .cmd。Windows 会通过 cmd.exe 执行它，
    # 同时绕过 PowerShell 对 dsh.ps1 的执行策略限制。
    return [command, "web", "--no-open", "--host", host, "--port", port]


# ---- 停止 dsh：优先「优雅退出」，超时才强杀 ------------------------------
#
# dsh 是用 CREATE_NO_WINDOW 启动的，因此它拥有一个「不可见的 console」（这与
# DETACHED_PROCESS 不同，后者才是真的没有 console）。附加上那个 console 并投递
# CTRL_C_EVENT，dsh 就会收到 SIGINT → app.current.fiber.dispose() → 插件卸载
# → reme 插件 disposeAll() → **ReMe 自动记忆的待提交回合被强制冲刷**。
#
# 而 taskkill /F 走的是 TerminateProcess：销毁回调根本跑不到，那几轮对话就丢了。
# 所以这里先优雅、超时再强杀；dsh 内部 teardown 的预算是 5 秒，故留 8 秒余量。

GRACEFUL_STOP_WAIT_SEC = 8.0
_CTRL_C_EVENT = 0
_STD_HANDLES = (-10, -11, -12)  # STD_INPUT / STD_OUTPUT / STD_ERROR_HANDLE


def _restore_std_handles(k32, saved):
    """把标准句柄还原成「附加 console 之前」的样子。见 send_ctrl_c 的说明。"""
    for handle_id, value in saved:
        try:
            k32.SetStdHandle(handle_id, value)
        except Exception as exc:
            log(f"restore std handle {handle_id} failed: {exc}")


def send_ctrl_c(pid):
    """把 Ctrl+C 投给 pid 所属的 console。投出成功返回 True（不代表对方已退出）。

    ⚠️ 必须存/还原标准句柄：附加到目标 console 会把本进程的标准句柄重绑过去，脱离之后
    它们就成了失效句柄。而 subprocess 在 Windows 上要用 GetStdHandle 的结果去做
    _make_inheritable，于是之后**每一次** subprocess.run 都会抛 WinError 6（句柄无效）
    —— taskkill、reg query、dsh.cmd --help 校验全部报废。1.4 实测踩到过这个坑。
    """
    if os.name != "nt" or not pid:
        return False
    import ctypes
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AttachConsole.argtypes = [wintypes.DWORD]
        k32.AttachConsole.restype = wintypes.BOOL
        k32.FreeConsole.argtypes = []
        k32.FreeConsole.restype = wintypes.BOOL
        k32.GetStdHandle.argtypes = [wintypes.DWORD]
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        k32.SetStdHandle.restype = wintypes.BOOL
        k32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
        k32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        k32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL

        saved = [(handle_id, k32.GetStdHandle(handle_id)) for handle_id in _STD_HANDLES]
        k32.FreeConsole()  # 托盘程序本没有 console；保险起见先释放
        if not k32.AttachConsole(int(pid)):
            _restore_std_handles(k32, saved)
            return False
        try:
            # 先把自己设为忽略 Ctrl+C，否则这次广播会把 helper 自己一起打断
            k32.SetConsoleCtrlHandler(None, True)
            time.sleep(0.05)
            ok = bool(k32.GenerateConsoleCtrlEvent(_CTRL_C_EVENT, 0))
            time.sleep(0.05)
            k32.SetConsoleCtrlHandler(None, False)
            return ok
        finally:
            k32.FreeConsole()
            _restore_std_handles(k32, saved)
    except Exception as exc:
        log(f"send_ctrl_c failed pid={pid}: {exc}")
        return False


def force_terminate_process_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
    except Exception as exc:
        log(f"taskkill failed pid={pid}: {exc}")


def ctrl_c_once(ordered_pids):
    """对一组共享同一 console 的进程只广播一次 Ctrl+C。

    必须只投一次：GenerateConsoleCtrlEvent(..., 0) 是向**整个 console** 广播，而这
    组 pid（cmd.exe 包装与 node.exe 主体）本来就在同一个 console 里。给第二个 pid
    再投一次，在 dsh 眼里就是「第二次信号」→ forceExitOnce() 跳过落盘冲刷，正好毁掉
    这次等待的意义。所以投出一次就停。
    """
    for pid in ordered_pids:
        if send_ctrl_c(pid):
            return True
    return False


def stop_process_group(pids, graceful=True):
    """只投一次 Ctrl+C → 等整组退出 → 残留的才强杀。"""
    targets = {int(p) for p in pids if p}
    if not targets:
        return
    ordered = sorted(targets)
    if graceful and ctrl_c_once(ordered):
        deadline = time.monotonic() + GRACEFUL_STOP_WAIT_SEC
        while time.monotonic() < deadline:
            if not any(psutil.pid_exists(pid) for pid in ordered):
                log(f"graceful stop ok pids={ordered}")
                return
            time.sleep(0.2)
        log(f"graceful stop timeout pids={ordered}; falling back to taskkill /F")
    for pid in ordered:
        if psutil.pid_exists(pid):
            force_terminate_process_tree(pid)


def terminate_process_tree(pid, graceful=True):
    """单个 pid 的便捷入口。"""
    stop_process_group({pid} if pid else set(), graceful=graceful)


def clear_state(message=""):
    global MANAGED_PROCESS
    MANAGED_PROCESS = None
    set_state(phase="stopped", url="", pid=None, managed=False, message=message, last_output=[])


def report_command_configuration_error(detail, status="invalid"):
    global MANAGED_PROCESS
    MANAGED_PROCESS = None
    set_state(
        phase="error",
        pid=None,
        managed=False,
        command_status=status,
        message=detail,
        last_output=[],
    )
    if status == "missing":
        notify(i18n.t("notify_cmd_missing"))
    else:
        notify(i18n.t("notify_cmd_invalid"))
    update_menu()


def report_runtime_start_failure(command_path, detail):
    """区分 dsh.cmd/CLI 异常与 Web 服务本身的启动异常。"""
    global MANAGED_PROCESS
    valid, validation_detail = validate_dsh_command(command_path)
    MANAGED_PROCESS = None
    if not valid:
        message = i18n.t("notify_cmd_validate_failed", validation_detail)
        set_state(
            phase="error",
            pid=None,
            managed=False,
            command_status="invalid",
            message=message,
            last_output=[],
        )
        log(f"dsh command became invalid after start failure: {message}")
        notify(i18n.t("notify_cmd_start_failed"))
    else:
        message = i18n.t("notify_web_start_failed", detail)
        set_state(
            phase="error",
            pid=None,
            managed=False,
            command_status="ready",
            message=message,
            last_output=[],
        )
        log(f"dsh web start failed after command validation: {message}")
        notify(message)
    update_menu()


def refresh_state():
    """刷新托盘中显示的状态，不主动启动或停止进程。"""
    global MANAGED_PROCESS
    # dsh 版本行也住缓存：升级（外部改包）后让下一次菜单重画读到新版本。
    _DSH_VERSION_CACHE["done"] = False
    current = state_copy()
    proc = MANAGED_PROCESS
    if proc is not None and proc.poll() is not None:
        log(f"managed dsh exited rc={proc.returncode}")
        MANAGED_PROCESS = None
        proc = None
        current = state_copy()

    if proc is not None and proc.poll() is None:
        url = current.get("url", "")
        if url and probe_url(url):
            set_state(phase="running", message="", pid=proc.pid, managed=True)
        else:
            discovered = discover_existing_dsh()
            if discovered:
                _pid, found_url, _cmdline = discovered
                set_state(phase="running", url=found_url, pid=proc.pid, managed=True, message="")
            elif current.get("phase") == "starting":
                set_state(phase="starting", pid=proc.pid, managed=True)
            else:
                set_state(phase="error", message=i18n.t("msg_process_no_response"), pid=proc.pid, managed=True)
        update_menu()
        return

    discovered = discover_existing_dsh()
    if discovered:
        pid, url, _cmdline = discovered
        set_state(phase="running", url=url, pid=pid, managed=False, message=i18n.t("msg_taken_over"))
    elif current.get("phase") not in ("starting", "stopping"):
        clear_state()
    update_menu()


def start_impl():
    global MANAGED_PROCESS
    refresh_state()
    current = state_copy()
    if current["phase"] in ("starting", "running"):
        notify(i18n.t("notify_already_running", current.get("url") or i18n.t("notify_starting")))
        return False
    try:
        command = build_dsh_command()
    except DshCommandError as exc:
        log(f"dsh command configuration error: {exc.detail}")
        report_command_configuration_error(exc.detail, exc.status)
        return False

    set_state(
        phase="starting",
        url="",
        pid=None,
        managed=True,
        message=i18n.t("msg_starting_now") + (f" ({LAUNCH_PORT_NOTE})" if LAUNCH_PORT_NOTE else ""),
        last_output=[],
    )
    update_menu()
    if LAUNCH_PORT_NOTE:
        notify(LAUNCH_PORT_NOTE)
    log("start: " + " ".join(command))
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            cwd=str(Path.home()),
        )
    except OSError as exc:
        message = i18n.t("notify_start_failed", exc)
        log(message)
        report_command_configuration_error(message, "invalid")
        return False
    except Exception as exc:
        message = i18n.t("notify_start_failed", exc)
        log(message)
        set_state(phase="error", message=message, pid=None, managed=False)
        notify(message)
        update_menu()
        return False

    MANAGED_PROCESS = proc
    set_state(pid=proc.pid, managed=True)
    threading.Thread(target=read_process_output, args=(proc,), daemon=True).start()

    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = "；".join(state_copy().get("last_output", [])[-3:])
            detail = i18n.t("err_validate_rc", proc.returncode)
            if tail:
                detail += f"：{tail[-240:]}"
            log(f"dsh process exited during startup: {detail}")
            report_runtime_start_failure(command[0], detail)
            return False
        current = state_copy()
        url = current.get("url", "")
        if url and probe_url(url):
            set_state(phase="running", message="", url=url, pid=proc.pid, managed=True)
            notify(i18n.t("notify_started", url))
            update_menu()
            open_panel(None, None)
            return True
        time.sleep(0.25)

    timeout_pids = {proc.pid}
    timeout_pids.update(listener_pids_for_port(url_port(state_copy().get("url", ""))))
    stop_process_group(timeout_pids)
    detail = i18n.t("notify_start_timeout")
    log(f"dsh startup timeout: {detail}")
    report_runtime_start_failure(command[0], detail)
    return False


def stop_impl():
    global MANAGED_PROCESS
    refresh_state()
    current = state_copy()
    pid = current.get("pid")
    if not pid:
        notify(i18n.t("notify_not_running"))
        return True
    set_state(phase="stopping", message=i18n.t("notify_stopping"))
    update_menu()
    log(f"stop pid={pid} managed={current.get('managed')}")
    stop_pids = {int(pid)}
    stop_pids.update(listener_pids_for_port(url_port(current.get("url", ""))))
    stop_process_group(stop_pids)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        remaining = listener_pids_for_port(url_port(current.get("url", "")))
        if not psutil.pid_exists(pid) and not remaining and not probe_url(current.get("url", "")):
            break
        time.sleep(0.25)
    MANAGED_PROCESS = None
    refresh_state()
    after = state_copy()
    if after.get("phase") == "running":
        message = i18n.t("notify_stop_failed")
        set_state(phase="error", message=message)
        notify(message)
        update_menu()
        return False
    clear_state()
    notify(i18n.t("notify_stopped"))
    update_menu()
    return True


def restart_impl():
    if not stop_impl():
        return
    time.sleep(0.5)
    start_impl()


def run_action(label, func):
    if not ACTION_LOCK.acquire(blocking=False):
        notify(i18n.t("notify_action_busy", label))
        return

    def worker():
        try:
            func()
        except Exception as exc:
            log(f"{label} failed: {exc}")
            set_state(phase="error", message=i18n.t("notify_action_failed", label, exc))
            notify(i18n.t("notify_action_failed", label, exc))
            update_menu()
        finally:
            ACTION_LOCK.release()

    threading.Thread(target=worker, name=f"dsh-{label}", daemon=True).start()


def _copy_text(text, success_key, empty_key="notify_no_url"):
    """通用剪贴板：成功/失败文案由调用方给键（URL 与升级命令共用一条链路）。"""
    if not text:
        notify(i18n.t(empty_key))
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.after(250, root.destroy)
        root.mainloop()
        notify(i18n.t(success_key))
    except Exception as exc:
        log(f"clipboard failed: {exc}")
        notify(i18n.t("notify_copy_failed", exc))


def copy_to_clipboard(text):
    _copy_text(text, "notify_url_copied")


def notify(message):
    log("notify: " + str(message))
    icon = TRAY_ICON
    if icon is not None:
        try:
            icon.notify(str(message), APP_NAME)
        except Exception as exc:
            log(f"notify failed: {exc}")


# ---- ：签名重画 + 菜单占用探测 -----------------------------------------
# 旧形态每拍无条件 icon.update_menu()，而 pystray 的重建是 DestroyMenu + CreatePopupMenu：
# 菜单正开着时重建 = 把它从用户手底下抽走（鼠标滑着滑着突然失焦）。改成：
#   状态提成签名 → 只有签名变了才重建 → 菜单开着时推迟，由 1.5s 补画拍补上。
# menu_is_open 探测器已随 tray_kit 2.3.0 下沉模板，此处不再内联。


def _menu_signature():
    """菜单上会「显示出来」的全部状态：只有它变了才值得重建（§状态唯一写入点）。

    **漏一项 = 那一项变了菜单不刷新**。逐项对照 build_menu()：
      · 信息行 status_line()   ← phase / command_status / managed / message / pid
      · dsh 版本行             ← 全局安装的 dsh 版本（升级后菜单自动刷新）
      · 地址行与「复制/打开面板」的 enabled ← url
      · 「下载并更新」的 enabled ← LATEST_VERSION
      · 启停重试的 enabled ← phase / pid（就绪判定用的也是 command_status）
      · 「退出」的 enabled ← STOP_EVENT
      · 自启 / 启动时自动启动 的 checked ← 注册表与 CFG
      · 状态刷新间隔子菜单的 checked ← 配置
      · 全部菜单文案 ← i18n.current_lang()
    STOP_EVENT 与 LATEST_VERSION 不是容器，但同样驱动 enabled，一并纳入。
    """
    state = state_copy()
    return (
        i18n.current_lang(),
        state["phase"], state["command_status"], state["managed"],
        state["message"], state["pid"], state["url"],
        dsh_version_line(),
        LATEST_VERSION is not None,
        autostart.is_autostart_enabled(),
        bool(CFG.get("start_on_launch", False)),
        current_status_refresh_interval(),
        STOP_EVENT.is_set(),
    )


def rebuild_menu():
    """MenuSignature 的落地动作：真正重画图标与菜单句柄（只由签名变化驱动）。"""
    icon = TRAY_ICON
    if icon is None:
        return
    tray_icons.tray_icons.set_state("running" if state_copy().get("phase") == "running" else "stopped")
    icon.menu = build_menu()
    icon.update_menu()


MENU_SIG = tray_kit.MenuSignature(rebuild_menu, menu_is_open=tray_kit.menu_is_open, log=log)


def update_menu():
    """状态变了就重画；菜单开着时自动推迟（由 menu_refresh_loop 的补画拍补上）。

    调用点遍布状态变更处，保持原签名不变——只是从「每次都重建」变成「签名变了才重建」。
    """
    MENU_SIG.update(_menu_signature())


def menu_refresh_loop():
    """1.5s 补画拍（tray_kit 三循环之②）：把「菜单开着时被推迟」的那次重画补上。

    同一拍里顺带消费 show.request：第二个实例被单实例守卫挡下后只写文件、不弹窗，
    常驻实例发现就删掉并弹一次「已在运行」气泡（本工具没有设置窗口）。
    """
    while not STOP_EVENT.wait(1.5):
        try:
            consume_show_request()
            update_menu()
            MENU_SIG.flush_deferred()
        except Exception as exc:
            log(f"menu refresh failed: {exc}")


# ---- / 落地·入口骨架：UI 队列封送 -------------------------------------------------
# tkinter 不是线程安全的。此前「选择 dsh.cmd」是在 threading.Thread 里直接 tk.Tk() 的
# （为了不阻塞托盘），等于**在工作线程里建/毁一个 Tk 解释器**——换一个 CPython/_tkinter
# 构建就可能崩，任何跨线程共享都会踩解释器状态。
# 改成：**所有 Tk 工作都投给同一个常驻线程**，工作线程投完等结果回来。
# 跨线程传递的只有「队列里的一个可调用对象」，Tk 对象从不离开它自己的线程。
ui_q = queue.Queue()
_UI_THREAD_NAME = "dsh-ui"
_ui_start_lock = threading.Lock()
_ui_thread = None


def ui_thread_loop():
    """唯一的 Tk 线程：顺序执行投进来的 UI 工作（daemon，进程退出即结束）。"""
    while True:
        ui_q.get()()


def ui_post(fn):
    """把 UI 工作封送到唯一的 Tk 线程执行，阻塞取回结果（落地·入口骨架）。

    线程**按需启动**：main() 会先起一个，但测试/诊断路径可能不经过 main()——
    那时若只 put 不等执行，就会永久卡在 done.wait()（实测踩到：test_update_chain
    直接调 quit_menu()，测试挂死）。所以这里补一次懒启动。
    已在 Tk 线程上时直接跑，否则自己投的活自己等 = 自锁。
    """
    global _ui_thread
    if threading.current_thread().name == _UI_THREAD_NAME:
        return fn()
    with _ui_start_lock:
        if _ui_thread is None or not _ui_thread.is_alive():
            _ui_thread = threading.Thread(target=ui_thread_loop, name=_UI_THREAD_NAME,
                                          daemon=True)
            _ui_thread.start()

    box, done = {}, threading.Event()

    def _job():
        try:
            box["result"] = fn()
        except BaseException as exc:    # 异常要原样回到调用方，不能吞成静默
            box["exc"] = exc
        finally:
            done.set()

    ui_q.put(_job)
    done.wait()
    if "exc" in box:
        raise box["exc"]
    return box.get("result")


def status_line():
    current = state_copy()
    command_status = current.get("command_status")
    if command_status == "checking":
        return i18n.t("status_checking_cmd")
    if command_status == "missing":
        return i18n.t("status_cmd_missing")
    if command_status == "invalid":
        return i18n.t("status_cmd_invalid")
    phase = current.get("phase")
    if phase == "running":
        return i18n.t("status_running")
    if phase == "starting":
        return i18n.t("status_starting")
    if phase == "stopping":
        return i18n.t("status_stopping")
    if phase == "error":
        return i18n.t("status_error")
    return i18n.t("status_stopped")


def display_url(url):
    """菜单展示用：token 全掩码。完整地址唯一入口 = 复制面板地址。"""
    return tray_kit.mask_token(url)


def url_line():
    url = state_copy().get("url")
    return i18n.t("tray_panel", display_url(url)) if url else i18n.t("tray_panel_none")


def has_url():
    return bool(state_copy().get("url"))


def dsh_command_ready():
    return state_copy().get("command_status") == "ready"


def open_panel(_icon, _item):
    refresh_state()
    url = state_copy().get("url")
    if not url:
        notify(i18n.t("notify_not_running"))
        return
    log(f"open panel: {url}")
    try:
        opened = webbrowser.open(url)
        if not opened:
            log("open panel returned false")
            notify(i18n.t("notify_panel_open_manual"))
    except Exception as exc:
        # The Web service is already healthy; a browser handoff failure should
        # not turn a successful dsh start into a false startup error.
        log(f"open panel failed: {exc}")
        notify(i18n.t("notify_panel_open_failed", exc))


def copy_panel_url(_icon, _item):
    refresh_state()
    # 剪贴板要临时建一个 Tk 根 → 同样封送到 Tk 线程（落地·入口骨架）。
    ui_post(lambda: copy_to_clipboard(state_copy().get("url", "")))


def configured_dsh_command():
    return configured_dsh_command_path()


def open_dsh_command_path(_icon, _item):
    candidate = configured_dsh_command()
    if candidate is None:
        notify(i18n.t("notify_cmd_not_set"))
        return
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    if candidate.is_file():
        try:
            subprocess.Popen(
                ["explorer.exe", f"/select,{candidate}"],
                creationflags=CREATE_NO_WINDOW,
            )
            return
        except OSError as exc:
            log(f"open dsh command path failed: {exc}")
            notify(i18n.t("notify_open_cmd_failed", exc))
            return
    if candidate.parent.is_dir():
        os.startfile(str(candidate.parent))  # noqa: S606
        notify(i18n.t("notify_cmd_file_missing"))
    else:
        notify(i18n.t("notify_cmd_path_missing"))


def start_menu(_icon, _item):
    run_action(i18n.t("action_start"), start_impl)


def stop_menu(_icon, _item):
    run_action(i18n.t("action_stop"), stop_impl)


def restart_menu(_icon, _item):
    run_action(i18n.t("action_restart"), restart_impl)


def rescan_menu(_icon, _item):
    threading.Thread(target=refresh_state, name="dsh-rescan", daemon=True).start()
    notify(i18n.t("notify_refreshed"))


def choose_dsh_command_menu(_icon, _item):
    # 对话框里有 tk.Tk()：必须走 ui_post 封送到唯一的 Tk 线程（落地·入口骨架）。
    # 仍然另起线程，是为了不让托盘在等用户选文件的这几秒里失去响应。
    threading.Thread(
        target=ui_post,
        args=(choose_dsh_command_worker,),
        name="dsh-choose-command",
        daemon=True,
    ).start()


def auto_detect_dsh_command_menu(_icon, _item):
    threading.Thread(
        target=auto_detect_dsh_command_worker,
        name="dsh-auto-detect-command",
        daemon=True,
    ).start()


def auto_detect_dsh_command_worker():
    run_auto_detect_dsh_command(startup=False)


def run_auto_detect_dsh_command(startup=False):
    if not COMMAND_LOCK.acquire(blocking=False):
        notify(i18n.t("notify_cmd_detecting"))
        return False
    try:
        set_state(command_status="checking", message=i18n.t("notify_cmd_detecting_now"))
        update_menu()
        notify(i18n.t("notify_cmd_autodetect_start"))
        candidates = discover_dsh_command_candidates()
        log(f"dsh command auto-detect candidates: {[str(path) for path in candidates]}")
        failures = []
        for candidate in candidates:
            valid, detail = validate_dsh_command(str(candidate))
            if valid:
                CFG["dsh_cmd"] = str(candidate)
                save_config()
                set_state(command_status="ready", message="")
                log(f"dsh command auto-detect succeeded and saved: {candidate}")
                notify(i18n.t("notify_cmd_autodetect_ok", candidate))
                update_menu()
                if startup and CFG.get("start_on_launch"):
                    run_action(i18n.t("action_start"), start_impl)
                return True
            failures.append(f"{candidate}: {detail}")
            log(f"dsh command auto-detect candidate failed: path={candidate} detail={detail}")

        status = "invalid" if failures else "missing"
        message = i18n.t("msg_no_cmd_found")
        set_state(command_status=status, message=message)
        if failures:
            log("dsh command auto-detect failed: " + " | ".join(failures))
        else:
            log("dsh command auto-detect failed: no candidates")
        notify(i18n.t("notify_cmd_autodetect_fail"))
        update_menu()
        return False
    finally:
        COMMAND_LOCK.release()


def choose_dsh_command_worker():
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        current = Path(str(CFG.get("dsh_cmd", "") or ""))
        initialdir = str(current.parent) if current.parent.is_dir() else str(Path.home())
        selected = filedialog.askopenfilename(
            title=i18n.t("dlg_choose_cmd_title"),
            initialdir=initialdir,
            initialfile="dsh.cmd",
            filetypes=[
                ("dsh.cmd", "dsh.cmd"),
                (i18n.t("dlg_filetype_cmd"), "*.cmd"),
                (i18n.t("dlg_filetype_all"), "*.*"),
            ],
            parent=root,
        )
    except Exception as exc:
        log(f"dsh command picker failed: {exc}")
        notify(i18n.t("notify_cmd_choose_failed", exc))
        return
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if not selected:
        return

    try:
        candidate = str(Path(selected).resolve())
        log(f"dsh command validation started: {candidate}")
        notify(i18n.t("notify_cmd_validating"))
        valid, detail = validate_dsh_command(candidate)
    except Exception as exc:
        log(f"dsh command validation crashed: path={selected} detail={exc}")
        time.sleep(VALIDATION_FAILURE_NOTIFY_DELAY_SEC)
        notify(i18n.t("notify_cmd_validate_failed", exc))
        return
    if not valid:
        log(f"dsh command validation failed: path={candidate} detail={detail}")
        # 文件名校验等失败可能在“正在校验”通知刚发出后立即返回；
        # 给 Windows 通知区域留出处理上一条消息的时间，避免失败提示被吞掉。
        time.sleep(VALIDATION_FAILURE_NOTIFY_DELAY_SEC)
        notify(i18n.t("notify_cmd_validate_failed", detail))
        return

    CFG["dsh_cmd"] = candidate
    save_config()
    set_state(command_status="ready", message="")
    log(f"dsh command validation succeeded and saved: {candidate}")
    notify(i18n.t("notify_cmd_valid_ok"))
    update_menu()


def build_dsh_command_menu():
    return pystray.Menu(
        pystray.MenuItem(
            i18n.t("menu_cmd_open_path"),
            open_dsh_command_path,
            enabled=lambda _item: configured_dsh_command() is not None,
        ),
        pystray.MenuItem(i18n.t("menu_cmd_autodetect"), auto_detect_dsh_command_menu),
        pystray.MenuItem(i18n.t("menu_cmd_choose"), choose_dsh_command_menu),
    )


def validate_dsh_command(path):
    candidate = Path(path)
    if candidate.name.casefold() != "dsh.cmd":
        return False, i18n.t("err_validate_name")
    try:
        if not candidate.is_file():
            return False, i18n.t("err_validate_not_file")
    except OSError as exc:
        return False, i18n.t("err_validate_unreadable", exc)

    try:
        result = subprocess.run(
            [str(candidate), "web", "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            cwd=str(candidate.parent),
            timeout=DSH_CMD_VALIDATE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, i18n.t("err_validate_timeout")
    except OSError as exc:
        return False, i18n.t("err_validate_exec", exc)

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = "；".join(line.strip() for line in output.splitlines()[-3:] if line.strip())
        return False, i18n.t("err_validate_rc", result.returncode) + (f": {detail[-180:]}" if detail else "")
    if not output:
        return False, i18n.t("err_validate_no_help")
    return True, i18n.t("msg_validate_ok")


def set_status_refresh_interval(_icon, _item, seconds):
    if seconds not in STATUS_REFRESH_INTERVAL_VALUES:
        return
    CFG["status_refresh_interval_sec"] = seconds
    save_config()
    label = i18n.t(dict(STATUS_REFRESH_INTERVAL_CHOICES)[seconds])
    notify(i18n.t("notify_interval_set", label))
    update_menu()


def build_status_refresh_interval_menu():
    return pystray.Menu(*[
        pystray.MenuItem(
            i18n.t(key),
            functools.partial(set_status_refresh_interval, seconds=seconds),
            checked=lambda _item, value=seconds: current_status_refresh_interval() == value,
        )
        for seconds, key in STATUS_REFRESH_INTERVAL_CHOICES
    ])


def open_log(_icon=None, _item=None):
    """打开日志目录：目录不可用时只记日志，绝不让菜单回调抛异常。"""
    try:
        os.startfile(str(LOG_DIR))  # noqa: S606
    except OSError as exc:
        log(f"open log dir failed: {exc}")


def open_config(_icon, _item):
    if not CONFIG_PATH.exists():
        save_config()
    os.startfile(str(CONFIG_PATH))  # noqa: S606


# 自启三件套（含稳定位指向与启动自愈）全部来自模板件 template/autostart；
# main.py 只保留托盘开关的 UI 反馈。数据·单一真源：状态真源 = HKCU Run，不再双写 config。


def toggle_autostart(_icon, _item):
    enabled = not autostart.is_autostart_enabled()
    autostart.set_autostart(enabled)
    ok = autostart.is_autostart_enabled() == enabled
    notify(i18n.t("notify_autostart_on") if enabled and ok
           else i18n.t("notify_autostart_off") if not enabled and ok
           else i18n.t("notify_autostart_failed"))
    update_menu()


def toggle_start_on_launch(_icon, _item):
    CFG["start_on_launch"] = not bool(CFG.get("start_on_launch", False))
    save_config()
    notify(i18n.t("notify_start_on_launch_on") if CFG["start_on_launch"] else i18n.t("notify_start_on_launch_off"))
    update_menu()


def toggle_language(_icon, _item):
    """中英切换：改语言 → 持久化 → 显式重建菜单。"""
    new_lang = "en" if i18n.current_lang() == "zh" else "zh"
    i18n.init(new_lang)
    i18n.save_language_to_config(CONFIG_PATH, new_lang)
    log(f"language switched: {new_lang}")
    notify(i18n.t("notify_lang_switched"))
    # 语言进了签名，update_menu() 就会真的重建；这里再显式补一次，保证「点了立刻变」，
    # 而不是等下一拍（菜单若正开着，MenuSignature 会推迟到补画拍）。
    update_menu()
    MENU_SIG.flush_deferred()


def quit_menu(icon, _item):
    # 重入守卫：等待优雅退出的这几秒里用户可能再点一次「退出」。第二个 Ctrl+C 会让
    # dsh 走 forceExitOnce() 跳过落盘冲刷，恰好毁掉这次等待的意义，所以直接忽略。
    if STOP_EVENT.is_set():
        log("quit ignored: already stopping")
        return
    # G4.1 条款 4：退出必须过确认框；取消/关窗不退出。降级链（G4.1 × G4.2 互补，
    # 禁止跳过确认）：富对话框失败 → 原生 askyesno（清理按持久化配置）→
    # 弹窗链路整个不可用（唯一豁免）→ 放行退出且不碰 dsh。
    def _decide_quit():
        """退出确认裁决 → (proceed, stop_service)。**三态必须分开**。

        2026-09-19 缺陷：链路不可用（None）与用户明确取消（{"go": False}）共用一个
        `return`，于是弹窗一坏用户就被锁死在工具里——ocx 1.2.2 实证：_internal 目录
        被掏空、Tk 读不到 init.tcl，点「退出」静默无反应，只能用任务管理器。
        「不可用 ⇒ 放行」不等于「默认 True」：确认框本身没被拆掉，问到就一定听用户的。
        """
        try:
            def _persist_quit_stop(value: bool) -> None:
                # G4.2 条款 5（2026-09-18 用户定）：勾选一变即持久化，不等「退出」点击
                CFG["quit_stop_dsh"] = bool(value)
                save_config()

            # 确认框要建 Tk 根 → 封送到唯一的 Tk 线程（落地·入口骨架）。
            # 降级链两级都在里面跑：富对话框失败就走原生 askyesno（同样在那一个线程上）。
            def _confirm():
                try:
                    return tray_kit.confirm_quit_dialog(
                        APP_NAME, i18n.t("quit_checkbox"),
                        bool(CFG.get("quit_stop_dsh", False)),
                        on_change=_persist_quit_stop,
                        body_text=i18n.t("quit_native_text"),
                        confirm_text=i18n.t("quit_confirm_yes"),
                        cancel_text=i18n.t("quit_confirm_no"))
                except Exception as exc:
                    log(f"quit dialog failed ({type(exc).__name__}: {exc}); "
                        f"falling back to native confirm")
                try:
                    import tkinter as _tk
                    from tkinter import messagebox as _mb
                    _root = _tk.Tk()
                    _root.withdraw()
                    _go = bool(_mb.askyesno(APP_NAME, i18n.t("quit_native_text")))
                    _root.destroy()
                    return {"go": _go, "stop_service": bool(CFG.get("quit_stop_dsh", False))}
                except Exception as exc2:
                    # Tk 运行时缺失 / 会话不可交互：这**不是**用户作答，交给调用方放行。
                    log(f"native confirm failed ({type(exc2).__name__}: {exc2})")
                    return None

            choice = ui_post(_confirm)
        except Exception as exc:
            log(f"quit confirm could not be marshalled ({type(exc).__name__}: {exc})")
            return True, False         # 链路不可用 ⇒ 放行退出，服务不动
        if choice is None:
            log("quit confirm unavailable; quitting without stopping the service")
            return True, False         # 同上：弹窗链路整个不可用
        if not choice.get("go"):
            log("quit cancelled by user")
            return False, False        # 用户明确取消 ⇒ 不退出（确认框照旧有效）
        return True, bool(choice.get("stop_service"))

    _proceed, stop_dsh = _decide_quit()   # 持久化已随勾选动作完成
    if not _proceed:
        return
    perform_quit(stop_dsh, icon=icon)


def perform_quit(stop_dsh: bool, icon=None) -> None:
    """退出执行体（确认框之后 / 更新弹窗「立即更新」直调，**不做二次确认**）。

    ``icon=`` 供测试/调用方传入当前托盘对象；缺省用模块级 ``TRAY_ICON``。
    """
    # G4.2 条款 4/5：服务放行是合法状态——只有勾选「同时停止」才停 dsh。
    current = state_copy()
    pids = set()
    if stop_dsh:
        if current.get("pid"):
            pids.add(int(current["pid"]))
        if MANAGED_PROCESS is not None and MANAGED_PROCESS.poll() is None:
            pids.add(int(MANAGED_PROCESS.pid))
    else:
        log("quit: dsh service left running (released by user choice)")
    # 收尾必须在 icon.stop() 之前同步做完：本进程一退出，daemon 线程立刻消失。
    # 这里给 dsh 最多 GRACEFUL_STOP_WAIT_SEC 秒优雅退出（Ctrl+C → dispose →
    # ReMe 冲刷）；投不出去或超时会自动退回 taskkill /F。
    # 代价：托盘图标会多停留几秒，这是刻意的。
    STOP_EVENT.set()
    if pids:
        set_state(phase="stopping", message=i18n.t("notify_stopping"))
        update_menu()
    log(f"quit requested; graceful stop pids={sorted(pids)}")
    if pids:
        stop_process_group(pids)
    target_icon = icon if icon is not None else TRAY_ICON
    if target_icon is not None:
        target_icon.stop()
    if pids:
        log("quit: managed dsh stopped")
    if PENDING_UPDATE_CMD:
        # 本进程退出后由脚本接管：等待 → robocopy 铺新版 → 重启新 exe → 自删。
        # 走 update_helper 的收尾件，不用 os.system('start …')：后者经 cmd 新建控制台，
        # 本进程是没有控制台的 GUI，退出时桌面会闪一下黑框；它还是 shell 字符串插值。
        # 该件用 CREATE_NO_WINDOW|DETACHED_PROCESS 让脚本脱离父进程继续跑完替换。
        if not update_helper.launch_pending_cmd(PENDING_UPDATE_CMD, log=log):
            log("quit: pending update script was NOT launched")


def monitor_loop():
    while not STOP_EVENT.wait(current_status_refresh_interval()):
        try:
            refresh_state()
        except Exception as exc:
            log(f"monitor failed: {exc}")


# 更新状态由**工具自持**，不读模板模块的可变全局：模板包曾用 `from .x import *`
# 把 PENDING_CMD 拷成静态副本，工具读到恒 None → apply.cmd 永不拉起；UPDATE_READY
# 也曾恒 None → "下载并更新"恒灰。稳定契约是**函数返回值**：
#   check_update() -> {"newer", "latest", ...}；download_and_prepare() -> 脚本路径。
LATEST_VERSION = None
PENDING_UPDATE_CMD = None


def check_update_menu(_icon=None, _item=None):
    """统一消息模式（模板 update_ui 1.1.0）：先「正在检查更新…」，1 秒后真查再发结果。"""
    def check():
        global LATEST_VERSION
        result = update_helper.check_update(VERSION, force=True)
        if result.get("newer"):
            LATEST_VERSION = result["latest"]
        elif not result.get("error"):
            LATEST_VERSION = None
        return result

    update_ui.run_check_flow(check, notify, VERSION, lang=i18n.current_lang(),
                             on_result=lambda _result: update_menu())

def download_update_menu(_icon=None, _item=None):
    """托盘「下载并更新」：统一交互（模板 update_ui）——立即弹窗 + 进度条 + 两个选项。

    不再用气泡/无反馈：点击后马上出现「正在下载更新…」，下载完弹窗刷新为完成态，
    给出「立即更新 / 稍后更新」，并写明需要退出软件后才能应用。
    """
    latest = LATEST_VERSION
    if not latest or not getattr(sys, "frozen", False):
        return
    threading.Thread(target=_download_update_worker, args=(latest,),
                     name="dsh-update-download", daemon=True).start()


def _ask_update_choice(download):
    """弹窗让用户选「立即更新 / 稍后更新」。

    单独成函数是为了**测试可替换**：测试不该为了验证接线去开真实窗口
    （窗口测试会污染构建机/CI 桌面，也容易挂住）。
    """
    return ui_post(lambda: update_ui.run_download_dialog(
        download, lang=i18n.current_lang(), app_name=APP_NAME, log=log,
        icon=resource_path("resources/icons/default/48.png")))


def _download_update_worker(latest):
    global PENDING_UPDATE_CMD

    def download(progress):
        # 下载/校验/暂存全在模板 update_helper；progress 供弹窗进度条。
        # 先记 pending：这样无论用户点「立即更新」还是关掉弹窗，退出路径都能拿到脚本。
        global PENDING_UPDATE_CMD
        path = update_helper.download_and_prepare(
            latest, APP_DIR, UPDATE_DIR, log=log, progress=progress)
        PENDING_UPDATE_CMD = path
        return path

    try:
        # 对话框必须在 Tk 线程；ui_post 会阻塞本工作线程直到用户做出选择。
        decision = _ask_update_choice(download)
    except Exception as exc:  # noqa: BLE001 - 弹窗链路失败要留痕，不能静默
        log(f"update dialog failed: {exc}")
        notify(i18n.t("notify_update_download_failed", exc))
        update_menu()
        return

    if decision == "apply":
        if not PENDING_UPDATE_CMD:
            log("update: apply requested but no pending script exists")
            notify(i18n.t("notify_update_pending_missing"))
            update_menu()
            return
        log("update: user chose 'apply now'; quitting to run the replacement script")
        perform_quit(stop_dsh=False)
        return
    if decision == "later":
        log("update: user chose 'later'; replacement script will run on next quit")
        if PENDING_UPDATE_CMD:
            notify(i18n.t("notify_update_ready"))
    update_menu()


# ---- dsh 升级助手：兼容性检查 / 报告 / 记录 / 菜单 --------------------------
# 边界（与产品约定）：**只做静态检查与引导**，不自动升级、不自动停机。判据与 dsh
# 运行时一致（见 dsh-app-boot 的 unmetRequirements），另读 profile 的 compatibility.json
# 精确豁免（dsh plugin allow-version 的落盘形态）。


def dsh_profile_dir(profile=DSH_UPGRADE_PROFILE):
    """dsh profile 目录：$DSH_HOME 优先，否则 ~/.dsh（与 dsh 运行时同一约定）。"""
    root = os.environ.get("DSH_HOME", "").strip() or str(Path.home() / ".dsh")
    return Path(root) / "profiles" / profile


def _profile_manifest(profile_dir):
    try:
        path = profile_dir / "package.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _profile_plugin_names(profile_dir):
    """插件名 = dependencies 全部 + dsh.profile.bundles 里**真实装在本 profile** 的。

    bundles 含官方包（@deepseek-ai/dsh-base 等），它们不在 profile 的 node_modules 里，
    用「有没有 package.json」自然过滤掉，避免报告里出现一堆官方包噪音。
    """
    manifest = _profile_manifest(profile_dir)
    if not isinstance(manifest, dict):
        return []
    names = []
    seen = set()
    for name in (manifest.get("dependencies") or {}):
        if isinstance(name, str) and name not in seen:
            seen.add(name)
            names.append(name)
    bundles = ((manifest.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    for name in bundles:
        if not isinstance(name, str) or name in seen:
            continue
        if (profile_dir / "node_modules" / name / "package.json").is_file():
            seen.add(name)
            names.append(name)
    return names


def _plugin_manifest(profile_dir, name):
    try:
        path = profile_dir / "node_modules" / name / "package.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_exemptions(profile_dir):
    """profile 的 compatibility.json：``{name@version: [exact runtime versions]}``。"""
    try:
        data = json.loads((profile_dir / "compatibility.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_exempted(exemptions, name, version, target):
    values = exemptions.get(f"{name}@{version}")
    return isinstance(values, list) and target in values


def _check_criteria_a(manifest, target):
    """判据 A：engines.dsh 独自决定；否则所有 @deepseek-ai/dsh(-*) peer 都必须满足。"""
    engines = manifest.get("engines")
    engines_dsh = engines.get("dsh") if isinstance(engines, dict) else None
    if engines_dsh is not None:
        if not isinstance(engines_dsh, str) or not engines_dsh.strip():
            return None, i18n.t("report_engine_invalid")
        return version_in_range(target, engines_dsh), i18n.t("report_engine_dsh", engines_dsh)
    peers = {k: v for k, v in (manifest.get("peerDependencies") or {}).items()
             if isinstance(k, str) and (k == "@deepseek-ai/dsh" or k.startswith("@deepseek-ai/dsh-"))}
    if not peers:
        return None, i18n.t("report_no_declaration")
    unmet = {}
    for peer_name, rng in peers.items():
        if not isinstance(rng, str):
            continue
        if rng.strip() in _WORKSPACE_PEERS:
            continue
        if not version_in_range(target, rng):
            unmet[peer_name] = rng
    if unmet:
        return False, i18n.t("report_peer_unmet",
                             ", ".join(f"{k}={v}" for k, v in unmet.items()))
    return True, i18n.t("report_peer_ok", len(peers))


def _check_criteria_c(manifest):
    """判据 C：dependencies 里硬编码 @deepseek-ai/dsh-* 宿主包（高风险，启动即崩）。"""
    deps = {k: v for k, v in (manifest.get("dependencies") or {}).items()
            if isinstance(k, str) and k.startswith("@deepseek-ai/dsh-")}
    if not deps:
        return True, i18n.t("report_no_hardcode")
    return False, i18n.t("report_deps_hardcoded",
                         ", ".join(f"{k}={v}" for k, v in deps.items()))


def _check_plugin(profile_dir, name, target, exemptions):
    """单个插件的四判据检查。B/D 初版标记未知（B 需人工对照 CHANGELOG，D 需目标清单）。"""
    missing = i18n.t("report_plugin_missing_manifest")
    manifest = _plugin_manifest(profile_dir, name)
    if not isinstance(manifest, dict):
        return {"name": name, "version": "", "a": None, "a_detail": missing,
                "b": None, "b_detail": i18n.t("report_manual_review"),
                "c": None, "c_detail": missing, "d": None,
                "d_detail": i18n.t("report_transitive_unknown"),
                "conclusion": "unknown", "batch": "B", "exempted": False, "detail": missing}
    version = str(manifest.get("version") or "").strip()
    a_passed, a_detail = _check_criteria_a(manifest, target)
    c_passed, c_detail = _check_criteria_c(manifest)
    exempted = _is_exempted(exemptions, name, version, target)
    if a_passed is False or c_passed is False:
        conclusion = "incompatible"
    elif a_passed is True and c_passed is True:
        conclusion = "compatible"
    else:
        conclusion = "unknown"
    if exempted and conclusion == "incompatible":
        conclusion = "compatible"
    detail_parts = [a_detail, c_detail]
    if exempted:
        detail_parts.append(i18n.t("report_exempted_note"))
    return {"name": name, "version": version,
            "a": a_passed, "a_detail": a_detail,
            "b": None, "b_detail": i18n.t("report_manual_review"),
            "c": c_passed, "c_detail": c_detail,
            "d": None, "d_detail": i18n.t("report_transitive_unknown"),
            "conclusion": conclusion,
            "batch": "A" if conclusion == "compatible" else "B",
            "exempted": exempted, "detail": "；".join(detail_parts)}


def check_plugin_compatibility(target_version, profile=DSH_UPGRADE_PROFILE):
    """对 profile 的每个插件跑四判据，返回报告数据（run_time_version 由调用方补）。"""
    profile_dir = dsh_profile_dir(profile)
    exemptions = _load_exemptions(profile_dir)
    plugins = [_check_plugin(profile_dir, name, target_version, exemptions)
               for name in _profile_plugin_names(profile_dir)]
    summary = {"compatible": 0, "incompatible": 0, "unknown": 0}
    for plugin in plugins:
        summary[plugin["conclusion"]] += 1
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "current_version": dsh_install_version() or "",
            "target_version": target_version,
            "runtime_version": "",
            "profile": profile,
            "profile_dir": str(profile_dir),
            "plugins": plugins,
            "summary": summary}


# ---- 报告：HTML（内联 CSS，无外部依赖；文案全走 i18n） ------------------------
_REPORT_CSS = (
    "body{font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;margin:24px;"
    "color:#222;line-height:1.5}"
    "h1{font-size:20px;margin:0 0 8px}"
    ".meta{color:#666;font-size:13px;margin-bottom:16px}"
    ".meta div{margin:2px 0}"
    ".summary{font-size:14px;margin-bottom:16px}"
    ".pass{color:#1a7f37;font-weight:600}"
    ".fail{color:#c0392b;font-weight:600}"
    ".unknown{color:#b8860b;font-weight:600}"
    "table{border-collapse:collapse;width:100%;font-size:13px}"
    "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}"
    "th{background:#f5f5f5}"
    ".mono{font-family:Consolas,Monaco,monospace}"
    ".section{margin-top:24px}"
    ".section h2{font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px}"
    ".batch{margin:8px 0}"
    ".risk{background:#fff8e1;border-left:4px solid #f0ad4e;padding:8px 12px}"
    ".next{background:#e8f5e9;border-left:4px solid #1a7f37;padding:8px 12px}"
)


def _report_cell(passed):
    if passed is True:
        return '<td class="pass">%s</td>' % html_escape(i18n.t("report_pass"))
    if passed is False:
        return '<td class="fail">%s</td>' % html_escape(i18n.t("report_fail"))
    return '<td class="unknown">%s</td>' % html_escape(i18n.t("report_na"))


def _report_row(plugin):
    conclusion_class = {
        "compatible": "pass", "incompatible": "fail", "unknown": "unknown",
    }[plugin["conclusion"]]
    conclusion_text = {
        "compatible": i18n.t("report_compatible"),
        "incompatible": i18n.t("report_incompatible"),
        "unknown": i18n.t("report_unknown"),
    }[plugin["conclusion"]]
    if plugin["exempted"]:
        conclusion_text += " (%s)" % html_escape(i18n.t("report_exempted"))
    return ("<tr><td>%s</td><td class=\"mono\">%s</td>%s%s%s%s"
            "<td class=\"%s\">%s</td><td>%s</td></tr>") % (
        html_escape(plugin["name"]), html_escape(plugin["version"]),
        _report_cell(plugin["a"]), _report_cell(plugin["b"]),
        _report_cell(plugin["c"]), _report_cell(plugin["d"]),
        conclusion_class, conclusion_text, html_escape(plugin["detail"]))


def build_report_html(data):
    """把检查数据渲染成单文件 HTML（表格 + 分批 + 风险提示）。"""
    t = i18n.t
    batch_a = [p for p in data["plugins"] if p["batch"] == "A"]
    batch_b = [p for p in data["plugins"] if p["batch"] == "B"]
    rows = "".join(_report_row(plugin) for plugin in data["plugins"])
    batch_a_text = ", ".join("%s@%s" % (p["name"], p["version"]) for p in batch_a) or t("report_none")
    batch_b_text = ", ".join("%s@%s" % (p["name"], p["version"]) for p in batch_b) or t("report_none")
    risk_lines = "<br>".join(html_escape(t(key)) for key in (
        "report_risk_static", "report_risk_manual_b", "report_risk_target_channel"))
    next_lines = "<br>".join(html_escape(t(key)) for key in (
        "report_next_guide", "report_next_check", "report_next_upgrade", "report_next_verify"))
    return "".join([
        '<!DOCTYPE html><html lang="', html_escape(i18n.current_lang()),
        '"><head><meta charset="utf-8"><title>', html_escape(t("report_title")),
        '</title><style>', _REPORT_CSS, '</style></head><body>',
        '<h1>', html_escape(t("report_title")), '</h1>',
        '<div class="meta">',
        '<div>', html_escape(t("report_generated_at")), ': <span class="mono">',
        html_escape(data["generated_at"]), '</span></div>',
        '<div>', html_escape(t("report_current_version")), ': <span class="mono">',
        html_escape(data["current_version"] or t("menu_dsh_version_missing")),
        '</span> &rarr; ', html_escape(t("report_target_version")),
        ': <span class="mono">', html_escape(data["target_version"]), '</span></div>',
        '<div>', html_escape(t("report_runtime_version")), ': <span class="mono">',
        html_escape(data["runtime_version"] or t("report_na")), '</span></div>',
        '<div>', html_escape(t("report_profile")), ': <span class="mono">',
        html_escape(data["profile"]), '</span> (<span class="mono">',
        html_escape(data["profile_dir"]), '</span>)</div>',
        '</div>',
        '<div class="summary">',
        '<span class="pass">', html_escape(t("report_compatible")), ': ',
        str(data["summary"]["compatible"]), '</span> &middot; ',
        '<span class="fail">', html_escape(t("report_incompatible")), ': ',
        str(data["summary"]["incompatible"]), '</span> &middot; ',
        '<span class="unknown">', html_escape(t("report_unknown")), ': ',
        str(data["summary"]["unknown"]), '</span>',
        '</div>',
        '<div class="section"><h2>', html_escape(t("report_plugin_details")), '</h2>',
        '<table><thead><tr>',
        '<th>', html_escape(t("report_plugin_col")), '</th>',
        '<th>', html_escape(t("report_version_col")), '</th>',
        '<th>A</th><th>B</th><th>C</th><th>D</th>',
        '<th>', html_escape(t("report_conclusion_col")), '</th>',
        '<th>', html_escape(t("report_detail_col")), '</th>',
        '</tr></thead><tbody>', rows, '</tbody></table></div>',
        '<div class="section"><h2>', html_escape(t("report_suggested_order")), '</h2>',
        '<div class="batch"><b>', html_escape(t("report_batch_a")), '</b><br>',
        html_escape(batch_a_text), '</div>',
        '<div class="batch"><b>', html_escape(t("report_batch_b")), '</b><br>',
        html_escape(batch_b_text), '</div></div>',
        '<div class="section risk"><b>', html_escape(t("report_risk_title")),
        '</b><br>', risk_lines, '</div>',
        '<div class="section next"><b>', html_escape(t("report_next_steps")),
        '</b><br>', next_lines, '</div>',
        '</body></html>',
    ])


def save_report(html):
    """报告存用户数据区（OTA 更新不覆盖）；返回路径。"""
    DSH_UPGRADE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = DSH_UPGRADE_REPORT_DIR / ("%s.html" % time.strftime("%Y%m%d-%H%M%S"))
    path.write_text(html, encoding="utf-8")
    return path


def open_report(path):
    try:
        os.startfile(str(path))  # noqa: S606 - 浏览器/默认程序打开
    except OSError as exc:
        log(f"open upgrade report failed: {exc}")
        notify(i18n.t("notify_open_failed", exc))


# ---- 升级记录：一个升级批次一个 Markdown，追加不覆盖 -----------------------
_UPGRADE_RECORD = {"path": None}


def _record_path_for(from_version, to_version):
    ts = time.strftime("%Y%m%d-%H%M%S")
    return DSH_UPGRADE_RECORD_DIR / ("%s-%s-to-%s.md" % (
        ts, from_version or "unknown", to_version or "unknown"))


def start_upgrade_record(from_version, to_version):
    """开一个升级批次记录（写表头）；返回路径。"""
    path = _record_path_for(from_version, to_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# " + i18n.t("record_title"),
        "",
        "- " + i18n.t("record_current") + ": " + (from_version or i18n.t("menu_dsh_version_missing")),
        "- " + i18n.t("record_target") + ": " + (to_version or ""),
        "- " + i18n.t("record_started_at") + ": " + time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    _UPGRADE_RECORD["path"] = path
    return path


def append_record(section, content):
    """追加到当前批次记录；没有批次时按当前版本开一个。"""
    path = _UPGRADE_RECORD["path"] or _record_path_for(dsh_install_version(), "")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n## %s\n%s\n" % (section, content))
    return path


def _record_summary(data):
    lines = [
        "- %s: %d" % (i18n.t("report_compatible"), data["summary"]["compatible"]),
        "- %s: %d" % (i18n.t("report_incompatible"), data["summary"]["incompatible"]),
        "- %s: %d" % (i18n.t("report_unknown"), data["summary"]["unknown"]),
    ]
    for plugin in data["plugins"]:
        conclusion = {
            "compatible": i18n.t("report_compatible"),
            "incompatible": i18n.t("report_incompatible"),
            "unknown": i18n.t("report_unknown"),
        }[plugin["conclusion"]]
        lines.append("- %s@%s: %s" % (plugin["name"], plugin["version"], conclusion))
    return "\n".join(lines)


# ---- 菜单接线 ---------------------------------------------------------------
def _ask_target_version_dialog(current):
    """目标版本输入弹窗（默认填当前安装版本）；返回字符串或 None。"""
    return simpledialog.askstring(
        i18n.t("dlg_upgrade_target_title"),
        i18n.t("dlg_upgrade_target_text", current or i18n.t("menu_dsh_version_missing")),
        initialvalue=current or "",
    )


def check_upgrade_compatibility_menu(_icon=None, _item=None):
    """托盘「检查插件兼容性」：静态检查 → HTML 报告 → 浏览器打开，全程后台线程。"""
    current = dsh_install_version() or ""
    target = ui_post(lambda: _ask_target_version_dialog(current))
    if not target or not str(target).strip():
        return
    target = str(target).strip()
    if not _parse_semver(target):
        notify(i18n.t("notify_upgrade_target_invalid", target))
        return
    notify(i18n.t("notify_upgrade_checking"))
    start_upgrade_record(current, target)
    threading.Thread(target=_compat_check_worker, args=(target,),
                     name="dsh-compat-check", daemon=True).start()


def _compat_check_worker(target):
    try:
        data = check_plugin_compatibility(target)
        # 运行态校验（dsh --version）是 Node 冷启动，只在这条后台线程里做。
        data["runtime_version"] = dsh_cli_version()
        path = save_report(build_report_html(data))
        append_record(i18n.t("record_section_check"), _record_summary(data))
        open_report(path)
        notify(i18n.t("notify_upgrade_report_ready", path))
        update_menu()
    except Exception as exc:  # noqa: BLE001 - 检查链路失败要留痕、要给用户结论
        log(f"upgrade compat check failed: {exc}")
        notify(i18n.t("notify_upgrade_check_failed", exc))


def copy_upgrade_command_menu(_icon=None, _item=None):
    """托盘「复制升级命令」：显式版本号的 npm 全局安装命令（不依赖 latest 通道）。"""
    current = dsh_install_version() or ""
    target = ui_post(lambda: _ask_target_version_dialog(current))
    if not target or not str(target).strip():
        return
    target = str(target).strip()
    if not _parse_semver(target):
        notify(i18n.t("notify_upgrade_target_invalid", target))
        return
    cmd = 'npm i -g "@deepseek-ai/dsh@%s"' % target
    start_upgrade_record(current, target)
    append_record(i18n.t("record_section_command"), cmd)
    ui_post(lambda: _copy_text(cmd, "notify_cmd_copied"))


def open_upgrade_guide(_icon=None, _item=None):
    path = APP_DIR / "doc" / "dsh-upgrade-guide.md"
    if not path.is_file():
        notify(i18n.t("notify_upgrade_doc_missing", str(path)))
        return
    try:
        os.startfile(str(path))  # noqa: S606
    except OSError as exc:
        log(f"open upgrade guide failed: {exc}")
        notify(i18n.t("notify_open_failed", exc))


def open_upgrade_records(_icon=None, _item=None):
    try:
        DSH_UPGRADE_RECORD_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DSH_UPGRADE_RECORD_DIR))  # noqa: S606
    except OSError as exc:
        log(f"open upgrade records failed: {exc}")
        notify(i18n.t("notify_open_failed", exc))


def build_dsh_upgrade_menu():
    return pystray.Menu(
        pystray.MenuItem(i18n.t("menu_upgrade_check"), check_upgrade_compatibility_menu),
        pystray.MenuItem(i18n.t("menu_upgrade_open_doc"), open_upgrade_guide),
        pystray.MenuItem(i18n.t("menu_upgrade_open_records"), open_upgrade_records),
        pystray.MenuItem(i18n.t("menu_upgrade_copy_cmd"), copy_upgrade_command_menu),
    )


def build_menu():
    """家族标准八段式：信息 → 更新 → 默认入口 → 服务控制 → 业务 → 打开 → 偏好 → 退出。"""
    return pystray.Menu(
        # ① 信息区（只读）
        pystray.MenuItem(lambda _item: f"{APP_NAME} v{VERSION}", None, enabled=False),
        pystray.MenuItem(lambda _item: dsh_version_line(), None, enabled=False),
        pystray.MenuItem(lambda _item: status_line(), None, enabled=False),
        pystray.MenuItem(lambda _item: url_line(), None, enabled=False),
        #复制紧贴地址行，是获取完整 URL（含 token）的唯一入口
        pystray.MenuItem(i18n.t("menu_copy_url"), copy_panel_url, enabled=lambda _item: has_url()),
        pystray.Menu.SEPARATOR,
        # ② 更新区
        pystray.MenuItem(i18n.t("menu_check_update"), check_update_menu),
        pystray.MenuItem(i18n.t("menu_update_now"), download_update_menu,
                         enabled=lambda _item: LATEST_VERSION is not None and getattr(sys, "frozen", False)),
        pystray.Menu.SEPARATOR,
        # ③ 默认入口（双击托盘）
        pystray.MenuItem(i18n.t("menu_open_panel"), open_panel, default=True, enabled=lambda _item: has_url()),
        pystray.Menu.SEPARATOR,
        # ④ 服务控制
        pystray.MenuItem(i18n.t("menu_start"), start_menu,
                         enabled=lambda _item: dsh_command_ready() and state_copy().get("phase") not in ("starting", "running", "stopping")),
        pystray.MenuItem(i18n.t("menu_stop"), stop_menu,
                         enabled=lambda _item: bool(state_copy().get("pid")) and state_copy().get("phase") in ("running", "starting", "error")),
        pystray.MenuItem(i18n.t("menu_restart"), restart_menu,
                         enabled=lambda _item: dsh_command_ready() and state_copy().get("phase") not in ("starting", "stopping")),
        pystray.MenuItem(i18n.t("menu_refresh"), rescan_menu),
        pystray.Menu.SEPARATOR,
        # ⑤ 业务区
        pystray.MenuItem(i18n.t("menu_dsh_cmd"), build_dsh_command_menu()),
        pystray.MenuItem(i18n.t("menu_dsh_upgrade"), build_dsh_upgrade_menu()),
        pystray.Menu.SEPARATOR,
        # ⑥ 打开区
        pystray.MenuItem(i18n.t("menu_open_config"), open_config),
        pystray.MenuItem(i18n.t("menu_open_logs"), open_log),
        pystray.Menu.SEPARATOR,
        # ⑦ 偏好区
        pystray.MenuItem(i18n.t("menu_autostart"), toggle_autostart, checked=lambda _item: autostart.is_autostart_enabled()),
        pystray.MenuItem(i18n.t("menu_start_on_launch"), toggle_start_on_launch,
                         checked=lambda _item: bool(CFG.get("start_on_launch", False))),
        pystray.MenuItem(i18n.t("menu_refresh_interval"), build_status_refresh_interval_menu()),
        pystray.MenuItem(i18n.t("menu_language"), toggle_language),
        pystray.Menu.SEPARATOR,
        # ⑧ 退出（恒最后）。停止进行中也禁止退出：否则会再投一次 Ctrl+C，被 dsh
        # 当成「第二次信号」而跳过落盘冲刷。启动中不禁止，用户仍可随时退出托盘。
        pystray.MenuItem(i18n.t("menu_quit"), quit_menu,
                         enabled=lambda _item: not STOP_EVENT.is_set() and state_copy().get("phase") != "stopping"),
    )


def _mutex_name_is_valid(app_id):
    """互斥体名合法性 = 模板探针 + ERROR_ACCESS_DENIED 容错。

    模板 `tray_kit.mutex_name_is_valid` 把 ERROR_ACCESS_DENIED(5) 判成非法，但
    **err=5 恰恰证明内核认得这个名字**：互斥体已存在，只是被另一个完整性级别更高
    的进程持有、当前进程（典型：DSH sandbox 给工作区打了 Low 强制完整性标签，
    release\\ 里的 exe 以 Low 运行）没权限打开。这与 ERROR_ALREADY_EXISTS(183)
    同为「名字合法」；真正的「名字非法」是 ERROR_INVALID_NAME(1200)，不会混淆。
    不修模板（submodule），只在冒烟这一侧补一次同源探针。
    """
    if tray_kit.mutex_name_is_valid(app_id):
        return True
    # 模板判非法 → 补一次探针。名字形状仍由模板的 mutex_name_ok 判定（单一真源）；
    # 名字串按模板 `_derive_mutex_name` 的口径重建（该函数未导出，无法直接调用）。
    name = "Local\\%s-single-instance" % app_id
    if not tray_kit.mutex_name_ok(name):
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    handle = k32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if handle:
        k32.CloseHandle(handle)
    return bool(handle) or err in (183, 5)   # ERROR_ALREADY_EXISTS / ERROR_ACCESS_DENIED


def smoke():
    """构建冒烟测试：验证依赖、配置、守卫探针和 dsh 命令发现，不启动常驻服务。"""
    try:
        # D3.1 / 判据·smoke不绕 守卫覆盖探针：名字合法性（不占锁、不弹窗）。守卫坏了 3 个月
        # 而构建全绿的根因就是探针缺失——冒烟必须与运行期守卫问同一个名字。
        if not _mutex_name_is_valid(APP_ID):
            raise RuntimeError("mutex name is illegal for %s" % APP_ID)
        command = resolve_dsh_command()
        if not command:
            for candidate in discover_dsh_command_candidates():
                valid, _detail = validate_dsh_command(str(candidate))
                if valid:
                    command = str(candidate)
                    break
        if not command:
            raise RuntimeError("dsh.cmd not found or failed web --help validation")
        port = pick_free_port()
        save_config()
        log_message = f"OK command={command} host={CFG['host']} effective_port={port}"
        (APP_DIR / "smoke.log").write_text(log_message, encoding="utf-8")
        print(log_message)
        return 0
    except Exception as exc:
        log_message = f"FAIL {exc}"
        (APP_DIR / "smoke.log").write_text(log_message, encoding="utf-8")
        print(log_message)
        return 1


def lang_audit():
    """自检（--lang-audit）：静态扫描本文件里未进 zh 词表的中文串。

    只查字面量（docstring 除外），命中即列出行号；退出码非 0 = 有遗漏，
    便于接进门禁。数据/标识符本就不该进词表，故只在 src/main.py 上跑。
    """
    import ast
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    source_path = Path(__file__)
    if not source_path.is_file():
        # 冻结态没有源码可扫（--lang-audit 是 dev/CI 门禁）。这里报告已加载的词表
        # 大小，顺带证明 locale 数据在打包态确实被解析到了。
        print(f"lang-audit: source not available in frozen build ({source_path.name})")
        print(f"lang-audit: loaded tables zh={len(i18n.TABLES['zh'])} en={len(i18n.TABLES['en'])}")
        return 0
    source = source_path.read_text(encoding="utf-8")
    known = set(i18n.TABLES["zh"].values())
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print(f"FAIL lang-audit: {exc}")
        return 1
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if any(0x4E00 <= ord(ch) <= 0x9FFF for ch in node.value) and node.value not in known:
            missing.append((node.lineno, node.value))
    for lineno, text in sorted(missing):
        print(f"MISSING L{lineno}: {text}")
    print(f"lang-audit: {len(missing)} untranslated Chinese literal(s); zh table={len(known)} entries")
    return 1 if missing else 0


# ---- 单实例：命名互斥体（tray_kit；名字不含版本号，跨版本互拦） ----------
# 旧行为是每双击一次就多一个托盘图标：多个图标各自监控同一台 dsh，状态互相矛盾，
# 而且每个实例的「退出」都会先停 dsh。互斥体由内核管理，进程消失即自动释放。


class TrayStartupError(RuntimeError):
    """托盘窗口创建失败（典型：受限终端/沙箱/远程会话里 PermissionError）。"""


def run_tray_icon(icon, setup) -> None:
    """进入托盘消息循环；失败包成可读错误，交给顶层把原因告诉用户。"""
    try:
        icon.run(setup=setup)
    except Exception as exc:  # noqa: BLE001 - 托盘失败要可读，不裸抛 traceback
        raise TrayStartupError(exc) from exc


def _startup_headless() -> bool:
    """无头入口（--smoke / --lang-audit）不弹窗：那里没有用户可看，弹窗只会挂住流水线。"""
    return any(flag in sys.argv for flag in ("--smoke", "--lang-audit"))


def _startup_failure_text(exc, detail) -> str:
    """把启动异常翻成用户能照着做的提示（含 traceback 尾部供维护者定位）。"""
    if isinstance(exc, TrayStartupError):
        lines = [i18n.t("startup_tray_failed"),
                 "",
                 i18n.t("startup_common_causes"),
                 i18n.t("startup_from_explorer"),
                 i18n.t("startup_retry_hint")]
    else:
        lines = [i18n.t("startup_failed_with", exc)]
    if LOG_INIT_ERROR is not None:
        lines.extend(["", i18n.t("startup_log_unavailable")])
    lines.extend(["", i18n.t("startup_details"), str(detail)[-1500:]])
    return "\n".join(lines)


def _write_startup_failure_log(text):
    """best-effort 写崩溃信息：日志目录 → 程序目录 → %TEMP%；全失败返回 None。"""
    temp_root = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    candidates = [
        LOG_DIR / (APP_ID + ".log"),
        APP_DIR / "startup-error.log",
        Path(temp_root) / (APP_ID + "-startup-error.log") if temp_root else None,
    ]
    for path in candidates:
        if path is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
            return path
        except Exception:  # noqa: BLE001 - 换下一个落点
            continue
    return None


def _message_box(text, title=None) -> bool:
    """MessageBoxW 兜底；任何失败都静默（没有桌面/没有 user32 时不能反过来崩）。"""
    title = title or i18n.t("startup_tray_title")
    if ctypes is None:
        return False
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _show_startup_failure(exc, detail) -> None:
    """启动失败的可读提示：尽力写日志 + MessageBoxW + stderr；本身绝不能抛。"""
    text = _startup_failure_text(exc, detail)
    try:
        _write_startup_failure_log(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        print(text, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass
    if not _startup_headless():
        try:
            _message_box(text)
        except Exception:  # noqa: BLE001
            pass


def request_show_existing() -> int:
    """第二个实例：不弹任何模态框，只写 show.request 提示常驻实例「已在运行」，静默退出。

    本工具没有设置窗口，所以没有可前置的窗口；写失败也静默返回 0。
    """
    try:
        SHOW_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        SHOW_REQUEST_PATH.write_text(time.strftime("%Y-%m-%dT%H:%M:%S\n"), encoding="utf-8")
        log(f"duplicate instance: asked the resident instance to show the balloon ({SHOW_REQUEST_PATH})")
    except OSError as exc:
        log(f"duplicate instance: cannot write {SHOW_REQUEST_PATH}: {exc}")
    return 0


def consume_show_request() -> bool:
    """常驻实例侧：消费 show.request → 删文件并弹一次「dsh-helper 已在运行」气泡。"""
    try:
        if not SHOW_REQUEST_PATH.exists():
            return False
        SHOW_REQUEST_PATH.unlink()
    except OSError as exc:  # noqa: BLE001 - 读不到就下一轮再看
        log(f"show request unreadable: {exc}")
        return False
    log("duplicate instance: showing the already-running balloon")
    notify(i18n.t("dup_running_short"))
    return True


def main() -> int:
    global TRAY_ICON
    if not tray_kit.acquire_single_instance("dsh-helper", log=log):
        # 第二个实例：不弹模态框（1.8.4 体验修复），只写请求让常驻实例提示一句。
        return request_show_existing()
    log(f"startup {APP_NAME} v{VERSION} (pid {os.getpid()})")
    # 判据·启动自证（§4.1.38 的**唯一正本样例**，勿自创变体）：`startup` 行之后紧跟两行
    # **解析后**的数据根与配置路径。为什么值得：`python -c` / heredoc 探针**不落盘**，
    # 源码扫描原理上覆盖不到它们；能定死归属的只有**产物自带的这行日志**。
    log("data root: %s" % USER_DATA_DIR)
    log("config   : %s" % CONFIG_PATH)
    # exe-delete-guard（paths 1.1.4，MUST-WIRE）：让"本实例的 exe 不可被删除/改名"由**内核**保证，
    # 而不是由纪律保证。持有的是一个**不含 FILE_SHARE_DELETE** 的句柄 ⇒ 删除方（构建脚本 /
    # 手工 `rm -r` / 未来的 --clean）会**大声失败**，而不是把正在运行的实例目录静默掏空
    # （2026-09-19 事故的形态：实例仍在其中运行时 release\<工具>-<版本>\ 被掏空）。
    # 必须在**托盘创建之前**调用——晚一步，那一步的窗口期就没有保护；句柄持有到进程结束
    # （故意不 close，寿命就是进程寿命）；拿不到只记一行日志，绝不拦住启动（D3.2）；
    # dev 态由模板自己跳过（保护 python.exe 无意义）。
    paths.hold_exe_delete_guard(log=log)
    # G4.1 条款 3/5：启动自愈——存量 Run 键指向的 exe 已消失（换版本目录被删）时，
    # 静默重写到当前正确位置（优先稳定安装位 INSTALL_EXE，见 template/autostart）。
    autostart.migrate_autostart(log=log)
    # 收尾：更新脚本在托盘退出后才跑，要是被打断（重启/被杀/半路消失），那份解压好的
    # 整包（实测 ~50MB/次）就烂在 %TEMP% 里没人知道——启动扫一次。只清一小时前的：
    # 正在进行的更新，其暂存目录是刚建的。清扫失败不抛，拦不住启动。
    swept = update_helper.sweep_stale_update_dirs()
    if swept:
        log(f"update housekeeping: swept {swept} stale update dir(s) from TEMP")
    # 收尾：上次更新失败的通知也只能等下次启动说（更新脚本自删了）。marker 读一次即删，
    # 所以先取出来；托盘还没建、通知发不出去，先留在闭包里，到 setup_tray 再发。
    # 模板返回的是中文人话串（详情它已自己写进 update.log），这里只取"失败过"这个事实，
    # 文案走 i18n，否则英文界面会弹出一句中文。
    failed_note = update_helper.pop_failed_update_note(UPDATE_DIR, log=log)
    needs_command_detection = command_needs_startup_detection()
    set_state(
        command_status="checking" if needs_command_detection else "ready",
        message=i18n.t("notify_cmd_detecting_now") if needs_command_detection else "",
    )
    save_config()
    refresh_state()
    tray_icons.tray_icons.init()
    # 初态必须按"此刻已解析出来的状态"算：refresh_state() 可能已经接管了正在运行的
    # dsh Web（phase=running），且那次 update_menu() 已把 running 签名记进 MENU_SIG；
    # 这里再硬编码 stopped 的话，之后没有状态变化 → 永不 rebuild → 图标永久灰鲸。
    initial_state = initial_tray_state()
    TRAY_ICON = pystray.Icon(
        "dsh-helper",
        icon=tray_icons.tray_icons.get(initial_state),
        title=APP_NAME,
        menu=build_menu(),
    )
    tray_icons.tray_icons.bind(TRAY_ICON, initial_state)
    threading.Thread(target=monitor_loop, name="dsh-monitor", daemon=True).start()
    # 第②拍：菜单开着时被推迟的重画在这里补上（状态刷新间隔最长 600s，不能靠它）。
    threading.Thread(target=menu_refresh_loop, name="dsh-menu-refresh", daemon=True).start()
    # /落地·入口骨架：唯一的 Tk 线程，所有对话框/剪贴板都投给它（tkinter 非线程安全）。
    # 这里先起一个；ui_post() 也会按需懒启动，覆盖不经过 main() 的路径。
    ui_post(lambda: None)

    def startup_update_check():
        global LATEST_VERSION
        time.sleep(8)
        result = update_helper.check_update(VERSION, force=False)
        if result.get("newer"):
            LATEST_VERSION = result["latest"]
            notify(i18n.t("notify_update_available_menu", result["latest"], VERSION))

    threading.Thread(target=startup_update_check, name="dsh-update-check", daemon=True).start()

    def setup_tray(_icon):
        # 传入自定义 setup 后，pystray 不会再自动设置 visible=True。
        # 必须显式显示图标，否则进程会常驻但托盘中看不到入口。
        _icon.visible = True
        # 上次更新失败的通知：早退、无托盘时不提示，只有真起来了才说（见 main() 里的注释）。
        if failed_note:
            notify(i18n.t("notify_update_failed_prev"))
        if needs_command_detection:
            threading.Thread(
                target=lambda: run_auto_detect_dsh_command(startup=True),
                name="dsh-startup-command-detect",
                daemon=True,
            ).start()
        elif CFG.get("start_on_launch"):
            threading.Thread(target=lambda: run_action(i18n.t("action_start"), start_impl), daemon=True).start()

    try:
        run_tray_icon(TRAY_ICON, setup_tray)
    except TrayStartupError as exc:
        detail = traceback.format_exc()
        try:
            log("fatal startup error:\n" + detail)
        except Exception:  # noqa: BLE001 - 日志已兜底；这里也不能再抛
            pass
        _show_startup_failure(exc, detail)
        STOP_EVENT.set()
        return 2
    STOP_EVENT.set()
    return 0


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(smoke())
    if "--lang-audit" in sys.argv:
        raise SystemExit(lang_audit())
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 启动失败必须给用户可读原因，而不是裸 traceback
        detail = traceback.format_exc()
        try:
            log("fatal application error:\n" + detail)
        except Exception:  # noqa: BLE001
            pass
        _show_startup_failure(exc, detail)
        raise SystemExit(2)
