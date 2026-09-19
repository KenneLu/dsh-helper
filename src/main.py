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
import tkinter as tk
from tkinter import filedialog
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import psutil
import pystray
from PIL import Image, ImageDraw, ImageOps

from modules import autostart, i18n, log_kit, paths, tray_kit, update_helper   # noqa: E402
from modules.appconfig import APP_ID, ICON_ASSET as ICON_ASSET_REL   # noqa: E402


APP_NAME = "dsh-helper"
VERSION = "1.8.1"
# 程序本体目录**不在本文件派生**：唯一出处是 T2 paths 的 APP_DIR（打包后 = exe 所在
# 目录，开发态 = 仓库根）。这里曾另有一份同名派生量（开发态 = src/），与 paths 分叉，
# 只在冻结态碰巧重合——于是 dev 下 ICON_ASSET 解析成 src/resources/img/… 取不到，
# _load_icon_base() 静默走兜底图，而构建与冒烟全绿。
# 用户数据区/配置/日志/更新暂存同样出自 T2 paths（数据区住 LOCALAPPDATA，
# 1.6 及以前的 exe 旁旧配置由播种自动迁入）。
from modules.paths import (APP_DIR, CONFIG_PATH, LEGACY_CONFIG_PATH, LOG_DIR, LOG_PATH,
                           UPDATE_DIR, USER_DATA_DIR)
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

DEFAULT_CONFIG = {
    "dsh_cmd": "",
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    "status_refresh_interval_sec": DEFAULT_STATUS_REFRESH_INTERVAL_SEC,
    "start_on_launch": False,
    # 自启状态不落 config（F2-01 单一真源 = HKCU Run）：注册表是唯一出处，
    # 托盘勾选直接读注册表，见 modules/autostart。
    # G4.2 条款 5：退出清理勾选，持久化、默认不勾——不勾 = dsh 服务放行继续运行
    "quit_stop_dsh": False,
}

LOG_DIR.mkdir(parents=True, exist_ok=True)
_logger = log_kit.get_logger(LOG_DIR)   # T12：滚动 1MB×3（house 标准 D13）


def log(*parts):
    """模板件的日志契约是 **print 形态**（`log("下载中", name)`），与 log_kit 的单参闭包不同。

    `modules/update_helper` 里有 6 处多参调用；传单参的 log 进去，它们会在**真路径**上
    TypeError —— 而命中的正是"每次下载"(L407)、"每次成功拉起替换脚本"(L257)、
    "存在失败 marker 时"(L390) 这类必然会走到的行。dsh 的更新链因此从来没跑通过。
    这里按契约收任意个参数再拼接（模板 log_kit 待 tpl-keeper 统一为同一形态）。
    """
    _logger.info(" ".join(str(part) for part in parts))


def load_config():
    paths.seed_config()   # T2：exe 旁旧配置一次性迁入用户数据区
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
    # 下次 save_config 落盘时自然消失（F2-01）。
    return merged


CFG = load_config()
# T5：语言在配置读取之后、任何 t() 之前初始化（auto 跟随 Windows UI 语言）。
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


def copy_to_clipboard(text):
    if not text:
        notify(i18n.t("notify_no_url"))
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.after(250, root.destroy)
        root.mainloop()
        notify(i18n.t("notify_url_copied"))
    except Exception as exc:
        log(f"clipboard failed: {exc}")
        notify(i18n.t("notify_copy_failed", exc))


def notify(message):
    log("notify: " + str(message))
    icon = TRAY_ICON
    if icon is not None:
        try:
            icon.notify(str(message), APP_NAME)
        except Exception as exc:
            log(f"notify failed: {exc}")


# ---- E2-09：签名重画 + 菜单占用探测 -----------------------------------------
# 旧形态每拍无条件 icon.update_menu()，而 pystray 的重建是 DestroyMenu + CreatePopupMenu：
# 菜单正开着时重建 = 把它从用户手底下抽走（鼠标滑着滑着突然失焦）。改成：
#   状态提成签名 → 只有签名变了才重建 → 菜单开着时推迟，由 1.5s 补画拍补上。
GUI_INMENUMODE = 0x00000004


def menu_is_open():
    """系统弹出菜单是否正开着（E2-09）。

    探测：菜单模态标记 GUI_INMENUMODE 挂在**调用 TrackPopupMenu 的那个线程**上，
    遍历本进程线程去问；再以「前台窗口是系统菜单类 #32768」兜底。探测失败当没开着
    （宁可多重建一次，也不能因为探测失败就永远不重建）。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]

        user32 = ctypes.windll.user32
        for thread in threading.enumerate():
            tid = getattr(thread, "native_id", None)
            if not tid:
                continue
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not user32.GetGUIThreadInfo(int(tid), ctypes.byref(info)):
                continue
            if info.flags & GUI_INMENUMODE:
                return True
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            name = ctypes.create_unicode_buffer(32)
            user32.GetClassNameW(hwnd, name, 32)
            if name.value == "#32768":
                return True
    except Exception:
        return False
    return False


def _menu_signature():
    """菜单上会「显示出来」的全部状态：只有它变了才值得重建（E2-09/§D6）。

    **漏一项 = 那一项变了菜单不刷新**。逐项对照 build_menu()：
      · 信息行 status_line()   ← phase / command_status / managed / message / pid
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
    icon.icon = make_icon_image(state_copy().get("phase") == "running")
    icon.menu = build_menu()
    icon.update_menu()


MENU_SIG = tray_kit.MenuSignature(rebuild_menu, menu_is_open=menu_is_open, log=log)


def update_menu():
    """状态变了就重画；菜单开着时自动推迟（由 menu_refresh_loop 的补画拍补上）。

    调用点遍布状态变更处，保持原签名不变——只是从「每次都重建」变成「签名变了才重建」。
    """
    MENU_SIG.update(_menu_signature())


def menu_refresh_loop():
    """1.5s 补画拍（tray_kit 三循环之②）：把「菜单开着时被推迟」的那次重画补上。"""
    while not STOP_EVENT.wait(1.5):
        try:
            update_menu()
            MENU_SIG.flush_deferred()
        except Exception as exc:
            log(f"menu refresh failed: {exc}")


# ---- E1-03 / I-03：UI 队列封送 -------------------------------------------------
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
    """把 UI 工作封送到唯一的 Tk 线程执行，阻塞取回结果（E1-03/I-03）。

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
    """菜单展示用：token 全掩码（T7/D11）。完整地址唯一入口 = 复制面板地址。"""
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
    # 剪贴板要临时建一个 Tk 根 → 同样封送到 Tk 线程（E1-03/I-03）。
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
    # 对话框里有 tk.Tk()：必须走 ui_post 封送到唯一的 Tk 线程（E1-03/I-03）。
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


def open_log(_icon, _item):
    os.startfile(str(LOG_DIR))  # noqa: S606


def open_config(_icon, _item):
    if not CONFIG_PATH.exists():
        save_config()
    os.startfile(str(CONFIG_PATH))  # noqa: S606


# 自启三件套（含稳定位指向与启动自愈）全部来自 T3 模板件 modules/autostart；
# main.py 只保留托盘开关的 UI 反馈。F2-01：状态真源 = HKCU Run，不再双写 config。


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
    """中英切换（T1）：改语言 → 持久化 → 显式重建菜单（D14）。"""
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

            # 确认框要建 Tk 根 → 封送到唯一的 Tk 线程（E1-03/I-03）。
            # 降级链两级都在里面跑：富对话框失败就走原生 askyesno（同样在那一个线程上）。
            def _confirm():
                try:
                    return tray_kit.confirm_quit_dialog(APP_NAME, i18n.t("quit_checkbox"),
                                                        bool(CFG.get("quit_stop_dsh", False)),
                                                        on_change=_persist_quit_stop)
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
    icon.stop()
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


def _load_icon_base():
    global _ICON_BASE
    if _ICON_BASE is not None:
        return _ICON_BASE.copy()
    try:
        source = _remove_baked_background(Image.open(ICON_ASSET).convert("RGBA"))
        alpha = source.getchannel("A")
        # 生成图边缘可能残留 alpha=1 的孤立像素；忽略这些像素后再裁剪，
        # 避免透明边缘把托盘图标的有效内容压小。
        trim_alpha = alpha.point(lambda value: 255 if value > 8 else 0)
        bbox = trim_alpha.getbbox()
        if bbox:
            source = source.crop(bbox)
        source.thumbnail((60, 60), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        canvas.alpha_composite(source, ((64 - source.width) // 2, (64 - source.height) // 2))
        _ICON_BASE = canvas
    except Exception as exc:
        log(f"icon asset load failed: {exc}")
        # 资源缺失时保留一个可识别的简易兜底图，避免托盘工具无法启动。
        canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((5, 16, 52, 48), fill=(18, 72, 145))
        draw.polygon([(45, 25), (61, 12), (55, 32), (61, 52), (45, 39)], fill=(18, 72, 145))
        draw.ellipse((43, 12, 60, 29), fill=(0, 151, 167))
        draw.ellipse((48, 16, 56, 24), fill=(255, 213, 0))
        _ICON_BASE = canvas
    return _ICON_BASE.copy()


def _remove_baked_background(image):
    """把生成图里误绘制的灰白棋盘格清成真正的透明背景。"""
    alpha = image.getchannel("A")
    if alpha.getextrema() == (0, 0):
        return image
    corners = [(0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)]
    for point in corners:
        r, g, b, a = image.getpixel(point)
        neutral = max(r, g, b) - min(r, g, b) <= 22
        light_background = min(r, g, b) >= 180
        dark_background = max(r, g, b) <= 28
        if a and neutral and (light_background or dark_background):
            ImageDraw.floodfill(image, point, (0, 0, 0, 0), thresh=48)
    return image


def _brighten_beacon(image):
    """运行态只做轻微提亮，保留图标资源本身的暖金色。"""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a and r >= 150 and g >= 100 and b <= 135 and r >= b + 80 and g >= b + 45:
                pixels[x, y] = (
                    min(255, int(r * 1.01 + 1)),
                    min(255, int(g * 1.04 + 4)),
                    max(0, int(b * 0.90)),
                    a,
                )
    return image


def make_icon_image(running):
    base = _load_icon_base()
    if running:
        return _brighten_beacon(base)
    gray = ImageOps.grayscale(base.convert("RGB"))
    gray = ImageOps.colorize(gray, black=(82, 82, 82), white=(205, 205, 205)).convert("RGBA")
    gray.putalpha(base.getchannel("A"))
    return gray


# 更新状态由**工具自持**，不读模板模块的可变全局：模板包曾用 `from .x import *`
# 把 PENDING_CMD 拷成静态副本，工具读到恒 None → apply.cmd 永不拉起；UPDATE_READY
# 也曾恒 None → "下载并更新"恒灰。稳定契约是**函数返回值**：
#   check_update() -> {"newer", "latest", ...}；download_and_prepare() -> 脚本路径。
LATEST_VERSION = None
PENDING_UPDATE_CMD = None


def check_update_menu(_icon=None, _item=None):
    def worker():
        global LATEST_VERSION
        result = update_helper.check_update(VERSION, force=True)
        if result.get("newer"):
            LATEST_VERSION = result["latest"]
            notify(i18n.t("notify_update_available", result["latest"], VERSION))
        elif result.get("error"):
            # 网络失败不动既有状态，避免误清已发现的新版本
            notify(i18n.t("notify_update_check_failed", result["error"]))
        else:
            LATEST_VERSION = None
            notify(i18n.t("notify_update_latest", VERSION))
        update_menu()
    threading.Thread(target=worker, daemon=True).start()


def download_update_menu(_icon=None, _item=None):
    global PENDING_UPDATE_CMD
    latest = LATEST_VERSION
    if not latest or not getattr(sys, "frozen", False):
        return

    def worker():
        global PENDING_UPDATE_CMD
        try:
            PENDING_UPDATE_CMD = update_helper.download_and_prepare(latest, APP_DIR, UPDATE_DIR, log=log)
            notify(i18n.t("notify_update_ready"))
        except Exception as exc:
            log(f"update download failed: {exc}")
            notify(i18n.t("notify_update_download_failed", exc))
        update_menu()
    threading.Thread(target=worker, daemon=True).start()


def build_menu():
    """house 标准八段式（执行文档 D14）：信息 → 更新 → 默认入口 → 服务控制 → 业务 → 打开 → 偏好 → 退出。"""
    return pystray.Menu(
        # ① 信息区（只读）
        pystray.MenuItem(lambda _item: f"{APP_NAME} v{VERSION}", None, enabled=False),
        pystray.MenuItem(lambda _item: status_line(), None, enabled=False),
        pystray.MenuItem(lambda _item: url_line(), None, enabled=False),
        # D11：复制紧贴地址行，是获取完整 URL（含 token）的唯一入口
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


def smoke():
    """构建冒烟测试：验证依赖、配置、守卫探针和 dsh 命令发现，不启动常驻服务。"""
    try:
        # D3.1 / C-10 守卫覆盖探针：名字合法性（不占锁、不弹窗）。守卫坏了 3 个月
        # 而构建全绿的根因就是探针缺失——冒烟必须与运行期守卫问同一个名字。
        if not tray_kit.mutex_name_is_valid(APP_ID):
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
    """T5/T1 自检（--lang-audit）：静态扫描本文件里未进 zh 词表的中文串。

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


# ---- 单实例：命名互斥体（T7 tray_kit；名字不含版本号，跨版本互拦） ----------
# 旧行为是每双击一次就多一个托盘图标：多个图标各自监控同一台 dsh，状态互相矛盾，
# 而且每个实例的「退出」都会先停 dsh。互斥体由内核管理，进程消失即自动释放。


def main():
    global TRAY_ICON
    if not tray_kit.acquire_single_instance("dsh-helper", log=log):
        log("another instance is already running; exiting")
        tray_kit.warn_duplicate_instance(APP_NAME, hint=i18n.t("dup_hint"))
        return
    log(f"startup {APP_NAME} v{VERSION} (pid {os.getpid()})")
    # G4.1 条款 3/5：启动自愈——存量 Run 键指向的 exe 已消失（换版本目录被删）时，
    # 静默重写到当前正确位置（优先稳定安装位 INSTALL_EXE，见 modules/autostart）。
    autostart.migrate_autostart(log=log)
    # T4 收尾：更新脚本在托盘退出后才跑，要是被打断（重启/被杀/半路消失），那份解压好的
    # 整包（实测 ~50MB/次）就烂在 %TEMP% 里没人知道——启动扫一次。只清一小时前的：
    # 正在进行的更新，其暂存目录是刚建的。清扫失败不抛，拦不住启动。
    swept = update_helper.sweep_stale_update_dirs()
    if swept:
        log(f"update housekeeping: swept {swept} stale update dir(s) from TEMP")
    # T4 收尾：上次更新失败的通知也只能等下次启动说（更新脚本自删了）。marker 读一次即删，
    # 所以先取出来；托盘还没建、通知发不出去，先留在闭包里，到 setup_tray 再发。
    # 模板返回的是中文人话串（详情它已自己写进 update.log），这里只取"失败过"这个事实，
    # 文案走 i18n，否则英文界面会弹出一句中文（T1 回归）。
    failed_note = update_helper.pop_failed_update_note(UPDATE_DIR, log=log)
    needs_command_detection = command_needs_startup_detection()
    set_state(
        command_status="checking" if needs_command_detection else "ready",
        message=i18n.t("notify_cmd_detecting_now") if needs_command_detection else "",
    )
    save_config()
    refresh_state()
    TRAY_ICON = pystray.Icon(
        "dsh-helper",
        icon=make_icon_image(state_copy().get("phase") == "running"),
        title=APP_NAME,
        menu=build_menu(),
    )
    threading.Thread(target=monitor_loop, name="dsh-monitor", daemon=True).start()
    # E2-09 第②拍：菜单开着时被推迟的重画在这里补上（状态刷新间隔最长 600s，不能靠它）。
    threading.Thread(target=menu_refresh_loop, name="dsh-menu-refresh", daemon=True).start()
    # E1-03/I-03：唯一的 Tk 线程，所有对话框/剪贴板都投给它（tkinter 非线程安全）。
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

    TRAY_ICON.run(setup=setup_tray)
    STOP_EVENT.set()


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(smoke())
    if "--lang-audit" in sys.argv:
        raise SystemExit(lang_audit())
    main()
