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

from modules import log_kit, paths, tray_kit, update_helper   # noqa: E402


APP_NAME = "dsh-helper"
VERSION = "1.8.1"
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
# 用户数据区/配置/日志/更新暂存：唯一出处是 T2 paths（数据区住 LOCALAPPDATA，
# 1.6 及以前的 exe 旁旧配置由播种自动迁入）。
from modules.paths import (CONFIG_PATH, LEGACY_CONFIG_PATH, LOG_DIR, LOG_PATH,
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


ICON_ASSET = resource_path("resources/img/dsh-helper-icon.png")
_ICON_BASE = None

STATUS_REFRESH_INTERVAL_CHOICES = (
    (5, "5秒"),
    (20, "20秒"),
    (60, "1分钟"),
    (300, "5分钟"),
    (600, "10分钟"),
)
STATUS_REFRESH_INTERVAL_VALUES = {seconds for seconds, _label in STATUS_REFRESH_INTERVAL_CHOICES}
DEFAULT_STATUS_REFRESH_INTERVAL_SEC = 60
VALIDATION_FAILURE_NOTIFY_DELAY_SEC = 2.0
# 默认固定端口：与 dsh web 官方兜底端口（3080）一致；该端口被占用时本次回退为自动端口。
DEFAULT_PORT = 3080

DEFAULT_CONFIG = {
    "dsh_cmd": "",
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    "status_refresh_interval_sec": DEFAULT_STATUS_REFRESH_INTERVAL_SEC,
    "start_on_launch": False,
    "autostart": False,
    # G4.2 条款 5：退出清理勾选，持久化、默认不勾——不勾 = dsh 服务放行继续运行
    "quit_stop_dsh": False,
}

LOG_DIR.mkdir(parents=True, exist_ok=True)
_logger = log_kit.get_logger(LOG_DIR)   # T12：滚动 1MB×3（house 标准 D13）


def log(message):
    _logger.info(message)


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
    merged["autostart"] = bool(merged.get("autostart", False))
    return merged


CFG = load_config()


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
                    "autostart": bool(CFG.get("autostart", False)),
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
    note = f"端口 {requested} 已被占用，本次改用自动端口"
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
        raise DshCommandError("尚未配置 dsh.cmd 路径", "missing")
    if candidate.name.casefold() != "dsh.cmd":
        raise DshCommandError("配置文件中的文件名不是 dsh.cmd", "invalid")
    try:
        if not candidate.is_file():
            raise DshCommandError("配置的 dsh.cmd 文件不存在", "invalid")
    except OSError as exc:
        raise DshCommandError(f"无法读取配置的 dsh.cmd：{exc}", "invalid") from exc

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
        notify("尚未配置 dsh.cmd，请执行自动检测或选择路径")
    else:
        notify("dsh.cmd 路径异常，请检查路径或执行自动检测")
    update_menu()


def report_runtime_start_failure(command_path, detail):
    """区分 dsh.cmd/CLI 异常与 Web 服务本身的启动异常。"""
    global MANAGED_PROCESS
    valid, validation_detail = validate_dsh_command(command_path)
    MANAGED_PROCESS = None
    if not valid:
        message = f"dsh.cmd 校验失败：{validation_detail}"
        set_state(
            phase="error",
            pid=None,
            managed=False,
            command_status="invalid",
            message=message,
            last_output=[],
        )
        log(f"dsh command became invalid after start failure: {message}")
        notify("dsh.cmd 启动失败，请检查路径或执行自动检测")
    else:
        message = f"dsh Web 启动失败：{detail}"
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
                set_state(phase="error", message="进程存在，但面板尚未响应", pid=proc.pid, managed=True)
        update_menu()
        return

    discovered = discover_existing_dsh()
    if discovered:
        pid, url, _cmdline = discovered
        set_state(phase="running", url=url, pid=pid, managed=False, message="已接管已运行的 dsh")
    elif current.get("phase") not in ("starting", "stopping"):
        clear_state()
    update_menu()


def start_impl():
    global MANAGED_PROCESS
    refresh_state()
    current = state_copy()
    if current["phase"] in ("starting", "running"):
        notify(f"dsh 已在运行：{current.get('url') or '正在启动'}")
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
        message="正在启动…" + (f"（{LAUNCH_PORT_NOTE}）" if LAUNCH_PORT_NOTE else ""),
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
        message = f"dsh 启动失败：{exc}"
        log(message)
        report_command_configuration_error(message, "invalid")
        return False
    except Exception as exc:
        message = f"dsh 启动失败：{exc}"
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
            detail = f"退出码 {proc.returncode}"
            if tail:
                detail += f"：{tail[-240:]}"
            log(f"dsh process exited during startup: {detail}")
            report_runtime_start_failure(command[0], detail)
            return False
        current = state_copy()
        url = current.get("url", "")
        if url and probe_url(url):
            set_state(phase="running", message="", url=url, pid=proc.pid, managed=True)
            notify(f"dsh 已启动：{url}")
            update_menu()
            open_panel(None, None)
            return True
        time.sleep(0.25)

    timeout_pids = {proc.pid}
    timeout_pids.update(listener_pids_for_port(url_port(state_copy().get("url", ""))))
    stop_process_group(timeout_pids)
    detail = "启动超时，已清理启动进程"
    log(f"dsh startup timeout: {detail}")
    report_runtime_start_failure(command[0], detail)
    return False


def stop_impl():
    global MANAGED_PROCESS
    refresh_state()
    current = state_copy()
    pid = current.get("pid")
    if not pid:
        notify("dsh 当前未运行")
        return True
    set_state(phase="stopping", message="正在停止…")
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
        message = "停止失败：dsh 进程仍在运行"
        set_state(phase="error", message=message)
        notify(message)
        update_menu()
        return False
    clear_state()
    notify("dsh 已停止")
    update_menu()
    return True


def restart_impl():
    if not stop_impl():
        return
    time.sleep(0.5)
    start_impl()


def run_action(label, func):
    if not ACTION_LOCK.acquire(blocking=False):
        notify(f"已有操作正在执行：{label}")
        return

    def worker():
        try:
            func()
        except Exception as exc:
            log(f"{label} failed: {exc}")
            set_state(phase="error", message=f"{label}失败：{exc}")
            notify(f"{label}失败：{exc}")
            update_menu()
        finally:
            ACTION_LOCK.release()

    threading.Thread(target=worker, name=f"dsh-{label}", daemon=True).start()


def copy_to_clipboard(text):
    if not text:
        notify("当前没有可复制的面板地址")
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.after(250, root.destroy)
        root.mainloop()
        notify("面板地址已复制")
    except Exception as exc:
        log(f"clipboard failed: {exc}")
        notify(f"复制失败：{exc}")


def notify(message):
    log("notify: " + str(message))
    icon = TRAY_ICON
    if icon is not None:
        try:
            icon.notify(str(message), APP_NAME)
        except Exception as exc:
            log(f"notify failed: {exc}")


def update_menu():
    icon = TRAY_ICON
    if icon is not None:
        try:
            icon.icon = make_icon_image(state_copy().get("phase") == "running")
            icon.update_menu()
        except Exception:
            pass


def status_line():
    current = state_copy()
    command_status = current.get("command_status")
    if command_status == "checking":
        return "状态：正在检测 dsh.cmd"
    if command_status == "missing":
        return "状态：未找到 dsh.cmd"
    if command_status == "invalid":
        return "状态：dsh.cmd 路径异常"
    phase = current.get("phase")
    if phase == "running":
        return "状态：运行中"
    if phase == "starting":
        return "状态：启动中"
    if phase == "stopping":
        return "状态：停止中"
    if phase == "error":
        return "状态：异常"
    return "状态：已停止"


def display_url(url):
    """菜单展示用：token 全掩码（T7/D11）。完整地址唯一入口 = 复制面板地址。"""
    return tray_kit.mask_token(url)


def url_line():
    url = state_copy().get("url")
    return f"当前面板：{display_url(url)}" if url else "当前面板：未启动"


def has_url():
    return bool(state_copy().get("url"))


def dsh_command_ready():
    return state_copy().get("command_status") == "ready"


def open_panel(_icon, _item):
    refresh_state()
    url = state_copy().get("url")
    if not url:
        notify("dsh 当前未运行")
        return
    log(f"open panel: {url}")
    try:
        opened = webbrowser.open(url)
        if not opened:
            log("open panel returned false")
            notify("dsh 已启动，但默认浏览器没有打开面板")
    except Exception as exc:
        # The Web service is already healthy; a browser handoff failure should
        # not turn a successful dsh start into a false startup error.
        log(f"open panel failed: {exc}")
        notify(f"dsh 已启动，但打开面板失败：{exc}")


def copy_panel_url(_icon, _item):
    refresh_state()
    copy_to_clipboard(state_copy().get("url", ""))


def configured_dsh_command():
    return configured_dsh_command_path()


def open_dsh_command_path(_icon, _item):
    candidate = configured_dsh_command()
    if candidate is None:
        notify("尚未配置 dsh.cmd 路径")
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
            notify(f"打开 dsh.cmd 路径失败：{exc}")
            return
    if candidate.parent.is_dir():
        os.startfile(str(candidate.parent))  # noqa: S606
        notify("配置的 dsh.cmd 文件不存在，已打开所在目录")
    else:
        notify("配置的 dsh.cmd 路径不存在")


def start_menu(_icon, _item):
    run_action("启动", start_impl)


def stop_menu(_icon, _item):
    run_action("停止", stop_impl)


def restart_menu(_icon, _item):
    run_action("重启", restart_impl)


def rescan_menu(_icon, _item):
    threading.Thread(target=refresh_state, name="dsh-rescan", daemon=True).start()
    notify("状态检测已刷新")


def choose_dsh_command_menu(_icon, _item):
    threading.Thread(
        target=choose_dsh_command_worker,
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
        notify("dsh.cmd 正在检测中")
        return False
    try:
        set_state(command_status="checking", message="正在检测 dsh.cmd…")
        update_menu()
        notify("正在自动检测 dsh.cmd…")
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
                notify(f"自动检测成功，已找到 dsh.cmd：{candidate}")
                update_menu()
                if startup and CFG.get("start_on_launch"):
                    run_action("启动", start_impl)
                return True
            failures.append(f"{candidate}: {detail}")
            log(f"dsh command auto-detect candidate failed: path={candidate} detail={detail}")

        status = "invalid" if failures else "missing"
        message = "未找到可用的 dsh.cmd"
        set_state(command_status=status, message=message)
        if failures:
            log("dsh command auto-detect failed: " + " | ".join(failures))
        else:
            log("dsh command auto-detect failed: no candidates")
        notify("自动检测失败：这台设备上没有找到可用的 dsh.cmd")
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
            title="选择 dsh.cmd",
            initialdir=initialdir,
            initialfile="dsh.cmd",
            filetypes=[
                ("dsh.cmd", "dsh.cmd"),
                ("命令脚本", "*.cmd"),
                ("所有文件", "*.*"),
            ],
            parent=root,
        )
    except Exception as exc:
        log(f"dsh command picker failed: {exc}")
        notify(f"选择 dsh.cmd 失败：{exc}")
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
        notify("正在校验 dsh.cmd…")
        valid, detail = validate_dsh_command(candidate)
    except Exception as exc:
        log(f"dsh command validation crashed: path={selected} detail={exc}")
        time.sleep(VALIDATION_FAILURE_NOTIFY_DELAY_SEC)
        notify(f"dsh.cmd 校验失败：{exc}")
        return
    if not valid:
        log(f"dsh command validation failed: path={candidate} detail={detail}")
        # 文件名校验等失败可能在“正在校验”通知刚发出后立即返回；
        # 给 Windows 通知区域留出处理上一条消息的时间，避免失败提示被吞掉。
        time.sleep(VALIDATION_FAILURE_NOTIFY_DELAY_SEC)
        notify(f"dsh.cmd 校验失败：{detail}")
        return

    CFG["dsh_cmd"] = candidate
    save_config()
    set_state(command_status="ready", message="")
    log(f"dsh command validation succeeded and saved: {candidate}")
    notify("dsh.cmd 校验成功，已保存到配置文件")
    update_menu()


def build_dsh_command_menu():
    return pystray.Menu(
        pystray.MenuItem(
            "打开路径",
            open_dsh_command_path,
            enabled=lambda _item: configured_dsh_command() is not None,
        ),
        pystray.MenuItem("自动检测", auto_detect_dsh_command_menu),
        pystray.MenuItem("选择路径", choose_dsh_command_menu),
    )


def validate_dsh_command(path):
    candidate = Path(path)
    if candidate.name.casefold() != "dsh.cmd":
        return False, "文件名必须是 dsh.cmd"
    try:
        if not candidate.is_file():
            return False, "文件不存在或不是文件"
    except OSError as exc:
        return False, f"无法读取文件：{exc}"

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
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "执行 web --help 超时（10秒）"
    except OSError as exc:
        return False, f"无法执行：{exc}"

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = "；".join(line.strip() for line in output.splitlines()[-3:] if line.strip())
        return False, f"退出码 {result.returncode}" + (f"：{detail[-180:]}" if detail else "")
    if not output:
        return False, "未返回 dsh Web 帮助信息"
    return True, "校验成功"


def set_status_refresh_interval(_icon, _item, seconds):
    if seconds not in STATUS_REFRESH_INTERVAL_VALUES:
        return
    CFG["status_refresh_interval_sec"] = seconds
    save_config()
    label = dict(STATUS_REFRESH_INTERVAL_CHOICES)[seconds]
    notify(f"状态刷新间隔已设为 {label}")
    update_menu()


def build_status_refresh_interval_menu():
    return pystray.Menu(*[
        pystray.MenuItem(
            label,
            functools.partial(set_status_refresh_interval, seconds=seconds),
            checked=lambda _item, value=seconds: current_status_refresh_interval() == value,
        )
        for seconds, label in STATUS_REFRESH_INTERVAL_CHOICES
    ])


def open_log(_icon, _item):
    os.startfile(str(LOG_DIR))  # noqa: S606


def open_config(_icon, _item):
    if not CONFIG_PATH.exists():
        save_config()
    os.startfile(str(CONFIG_PATH))  # noqa: S606


RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "dsh-helper"


def autostart_command():
    """set_autostart 会写进 Run 项的完整命令行。"""
    executable = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    return f'"{sys.executable}" "{executable}"'


def registered_autostart_command():
    """读回 Run 项里登记的命令行；没有该项返回 None。

    这个函数跑在菜单的 checked 回调里：一旦抛异常，整个菜单渲染都会失败。所以查询本身
    出错时只记日志并当作「未登记」，让勾选显示为未勾选，而不是打坏菜单。
    """
    try:
        result = subprocess.run(
            ["reg", "query", RUN_KEY, "/v", RUN_NAME],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        log(f"autostart registry query failed: {exc}")
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if "REG_SZ" in line:
            value = line.partition("REG_SZ")[2].strip()
            if value:
                return value
    return None


def _fold_command(text):
    """比对命令行用：忽略大小写、斜杠方向和多余空白。"""
    return os.path.normcase(os.path.expandvars(" ".join(str(text).split())))


def autostart_enabled():
    """勾选状态反映「下次开机真的会起来，而且起来的正是这一个」。

    只看 Run 项在不在会骗人：build.bat 每次都生成新的时间戳包目录，换了目录以后老项
    仍然存在，于是开机静默失败或悄悄拉起旧版本，而菜单却显示已开启。所以这里把登记的
    命令行一起比对；对不上就如实显示未勾选，由用户点一次写入正确路径（不会自动改写）。
    """
    registered = registered_autostart_command()
    if not registered:
        return False
    return _fold_command(registered) == _fold_command(autostart_command())


def set_autostart(enabled):
    if enabled:
        command = autostart_command()
        result = subprocess.run(
            ["reg", "add", RUN_KEY, "/v", RUN_NAME, "/t", "REG_SZ", "/d", command, "/f"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        ok = result.returncode == 0
    else:
        result = subprocess.run(
            ["reg", "delete", RUN_KEY, "/v", RUN_NAME, "/f"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        ok = result.returncode == 0
    CFG["autostart"] = bool(enabled and ok)
    save_config()
    notify("开机自启已开启" if enabled and ok else "开机自启已关闭" if not enabled and ok else "开机自启设置失败")
    update_menu()


def toggle_autostart(_icon, _item):
    set_autostart(not autostart_enabled())


def toggle_start_on_launch(_icon, _item):
    CFG["start_on_launch"] = not bool(CFG.get("start_on_launch", False))
    save_config()
    notify("启动时自动启动 dsh Web 已开启" if CFG["start_on_launch"] else "启动时自动启动 dsh Web 已关闭")
    update_menu()


def quit_menu(icon, _item):
    # 重入守卫：等待优雅退出的这几秒里用户可能再点一次「退出」。第二个 Ctrl+C 会让
    # dsh 走 forceExitOnce() 跳过落盘冲刷，恰好毁掉这次等待的意义，所以直接忽略。
    if STOP_EVENT.is_set():
        log("quit ignored: already stopping")
        return
    # G4.1 条款 4：退出必须过确认框；取消/关窗不退出。降级链（G4.1 × G4.2 互补，
    # 禁止跳过确认）：富对话框失败 → 原生 askyesno（清理按持久化配置）→
    # 原生也失败（纯托盘实例，唯一豁免）→ 放行退出且默认不清理。
    choice = None
    try:
        choice = tray_kit.confirm_quit_dialog(APP_NAME, "同时关闭当前 dsh 服务",
                                     bool(CFG.get("quit_stop_dsh", False)))
    except Exception as exc:
        log(f"quit dialog failed ({type(exc).__name__}: {exc}); falling back to native confirm")
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            _root = _tk.Tk()
            _root.withdraw()
            _go = bool(_mb.askyesno(APP_NAME, "确定退出 dsh-helper？清理选项按配置（退出后可在配置中修改）。"))
            _root.destroy()
            choice = {"go": _go, "stop_service": bool(CFG.get("quit_stop_dsh", False))}
        except Exception as exc2:
            log(f"native confirm failed ({type(exc2).__name__}: {exc2}); "
                f"proceeding without confirmation (dsh untouched by default)")
    if not choice or not choice.get("go"):
        log("quit cancelled by user")
        return
    stop_dsh = bool(choice.get("stop_service"))
    if CFG.get("quit_stop_dsh") != stop_dsh:
        CFG["quit_stop_dsh"] = stop_dsh
        save_config()
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
        set_state(phase="stopping", message="正在停止…")
        update_menu()
    log(f"quit requested; graceful stop pids={sorted(pids)}")
    if pids:
        stop_process_group(pids)
    icon.stop()
    if pids:
        log("quit: managed dsh stopped")
    if update_helper.PENDING_CMD:
        # 本进程退出后由脚本接管：等待 → robocopy 铺新版 → 重启新 exe → 自删
        os.system('start "" /min "%s"' % update_helper.PENDING_CMD)


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


def check_update_menu(_icon=None, _item=None):
    def worker():
        result = update_helper.check_update(VERSION, force=True)
        if result.get("newer"):
            notify(f"发现新版本 {result['latest']}（当前 {VERSION}），菜单「下载并更新」可用")
        elif result.get("error"):
            notify(f"检查更新失败：{result['error']}")
        else:
            notify(f"已是最新版本 {VERSION}")
        update_menu()
    threading.Thread(target=worker, daemon=True).start()


def download_update_menu(_icon=None, _item=None):
    latest = update_helper.UPDATE_READY
    if not latest or not getattr(sys, "frozen", False):
        return

    def worker():
        try:
            update_helper.download_and_prepare(latest, APP_DIR, UPDATE_DIR, log=log)
            notify("更新已就绪，退出托盘后将自动完成升级并重启")
        except Exception as exc:
            log(f"update download failed: {exc}")
            notify(f"下载更新失败：{exc}")
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
        pystray.MenuItem("复制面板地址", copy_panel_url, enabled=lambda _item: has_url()),
        pystray.Menu.SEPARATOR,
        # ② 更新区
        pystray.MenuItem("检查 dsh-helper 更新", check_update_menu),
        pystray.MenuItem("下载并更新 dsh-helper", download_update_menu,
                         enabled=lambda _item: update_helper.UPDATE_READY is not None and getattr(sys, "frozen", False)),
        pystray.Menu.SEPARATOR,
        # ③ 默认入口（双击托盘）
        pystray.MenuItem("打开 dsh 面板", open_panel, default=True, enabled=lambda _item: has_url()),
        pystray.Menu.SEPARATOR,
        # ④ 服务控制
        pystray.MenuItem("启动 dsh Web", start_menu,
                         enabled=lambda _item: dsh_command_ready() and state_copy().get("phase") not in ("starting", "running", "stopping")),
        pystray.MenuItem("停止 dsh Web", stop_menu,
                         enabled=lambda _item: bool(state_copy().get("pid")) and state_copy().get("phase") in ("running", "starting", "error")),
        pystray.MenuItem("重启 dsh Web", restart_menu,
                         enabled=lambda _item: dsh_command_ready() and state_copy().get("phase") not in ("starting", "stopping")),
        pystray.MenuItem("刷新状态", rescan_menu),
        pystray.Menu.SEPARATOR,
        # ⑤ 业务区
        pystray.MenuItem("dsh.cmd 路径", build_dsh_command_menu()),
        pystray.Menu.SEPARATOR,
        # ⑥ 打开区
        pystray.MenuItem("打开配置文件", open_config),
        pystray.MenuItem("打开日志目录", open_log),
        pystray.Menu.SEPARATOR,
        # ⑦ 偏好区
        pystray.MenuItem("开机自启", toggle_autostart, checked=lambda _item: autostart_enabled()),
        pystray.MenuItem("启动时自动启动 dsh Web", toggle_start_on_launch,
                         checked=lambda _item: bool(CFG.get("start_on_launch", False))),
        pystray.MenuItem("状态刷新间隔", build_status_refresh_interval_menu()),
        pystray.Menu.SEPARATOR,
        # ⑧ 退出（恒最后）。停止进行中也禁止退出：否则会再投一次 Ctrl+C，被 dsh
        # 当成「第二次信号」而跳过落盘冲刷。启动中不禁止，用户仍可随时退出托盘。
        pystray.MenuItem("退出", quit_menu,
                         enabled=lambda _item: not STOP_EVENT.is_set() and state_copy().get("phase") != "stopping"),
    )


def smoke():
    """构建冒烟测试：验证依赖、配置和 dsh 命令发现，不启动常驻服务。"""
    try:
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


# ---- 单实例：命名互斥体（T7 tray_kit；名字不含版本号，跨版本互拦） ----------
# 旧行为是每双击一次就多一个托盘图标：多个图标各自监控同一台 dsh，状态互相矛盾，
# 而且每个实例的「退出」都会先停 dsh。互斥体由内核管理，进程消失即自动释放。


def main():
    global TRAY_ICON
    if not tray_kit.acquire_single_instance("dsh-helper", log=log):
        log("another instance is already running; exiting")
        tray_kit.warn_duplicate_instance(APP_NAME,
                                         hint="请看任务栏右下角通知区域里的鲸鱼图标，本次启动已取消，不会多开一个托盘。")
        return
    log(f"startup {APP_NAME} v{VERSION} (pid {os.getpid()})")
    needs_command_detection = command_needs_startup_detection()
    set_state(
        command_status="checking" if needs_command_detection else "ready",
        message="正在检测 dsh.cmd…" if needs_command_detection else "",
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

    def startup_update_check():
        time.sleep(8)
        result = update_helper.check_update(VERSION, force=False)
        if result.get("newer"):
            notify(f"发现新版本 {result['latest']}（当前 {VERSION}），右键菜单可下载更新")

    threading.Thread(target=startup_update_check, name="dsh-update-check", daemon=True).start()

    def setup_tray(_icon):
        # 传入自定义 setup 后，pystray 不会再自动设置 visible=True。
        # 必须显式显示图标，否则进程会常驻但托盘中看不到入口。
        _icon.visible = True
        if needs_command_detection:
            threading.Thread(
                target=lambda: run_auto_detect_dsh_command(startup=True),
                name="dsh-startup-command-detect",
                daemon=True,
            ).start()
        elif CFG.get("start_on_launch"):
            threading.Thread(target=lambda: run_action("启动", start_impl), daemon=True).start()

    TRAY_ICON.run(setup=setup_tray)
    STOP_EVENT.set()


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(smoke())
    main()
