# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/tray_kit/tray_kit.py | TEMPLATE-VER: 2.0.1
"""T7｜托盘机制件：单实例互斥体、退出请求文件 + 监视循环、面板地址行掩码、菜单签名重画、退出确认框（2.0.0）。

蓝本：reme-helper（三循环/签名重画/退出纪律，执行文档 F13/D13/D14）与
local-speak2text（单实例/退出请求文件）。纯函数库：不依赖具体工具，导入即用。
设置窗口/主题等大件属二期（D3），不在此文件。
"""
import ctypes
import os
import re
import threading
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
_MUTEX_HANDLE = None


def acquire_single_instance(app_id, mutex_name=None, log=print):
    """命名互斥体钉死进程数为 1。返回 False = 已有实例。守卫自身失败时放行。"""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        handle = k32.CreateMutexW(None, False, mutex_name or f"Local\\{app_id}-single-instance")
        if not handle:
            raise OSError(f"CreateMutexW failed err={ctypes.get_last_error()}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle  # 故意持有到进程结束，不能提前关闭
        return True
    except Exception as exc:
        log(f"single-instance guard unavailable ({exc}); continuing")
        return True


def single_instance_free(mutex_name):
    """探测互斥体当前是否空着，**不持有**它（reme-helper 语义，1.0.1 沉淀）。

    诊断参数用它判断「此刻没有别的实例在跑」；自检进程不能顺手占锁，
    否则退出前用户紧接着启动的托盘会以为自己被抢了。
    """
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    handle = k32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return True
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    k32.CloseHandle(handle)
    return not already


def confirm_quit_dialog(app_name, checkbox_text, checked_init, parent=None, on_change=None):
    """退出确认 + 清理勾选对话框（G4.1 条款 4 / G4.2 条款 5；交互形态 = reme-helper 蓝本）。

    形态（家族标准，勿各自发挥）：标题 = app_name；正文「确定退出 <app_name>？
    勾选项会记住，下次退出沿用。」；单个 Checkbutton；退出钮红底 #E5534B flat 在左、
    取消 width=10 在右并持默认焦点；屏幕垂直 1/3 居中；模态 grab_set；
    Esc/关窗 = 取消（不退出）。

    parent：常驻 UI 线程的工具传 tk 父窗口；托盘菜单线程场景传 None（内部建临时
    Tk 根，wait_window 后销毁——对话框生命周期完全属于调用线程）。
    on_change(bool)：勾选状态一变即回调（2026-09-18 用户定：持久化跟随勾选动作，
    不等「退出」点击——点取消也已留存）。调用方在此落盘。
    返回 {"go": bool, "stop_service": bool}；取消返回 None。富对话框失败由调用方走
    降级链（原生 askyesno → 放行且默认不清理），本函数不吞异常。
    """
    import tkinter as tk

    if parent is not None:
        win = tk.Toplevel(parent)
        _temp_root = None
    else:
        _temp_root = tk.Tk()
        _temp_root.withdraw()
        win = tk.Toplevel(_temp_root)
    win.title(app_name)
    win.attributes("-topmost", True)
    win.resizable(False, False)
    result = {"go": False, "stop_service": bool(checked_init)}

    body = tk.Frame(win)
    body.pack(padx=18, pady=(14, 6))
    tk.Label(body, text=f"确定退出 {app_name}？勾选项会记住，下次退出沿用。",
             justify="left", wraplength=380).pack(anchor="w")
    opts = tk.Frame(win)
    opts.pack(anchor="w", padx=18, pady=(6, 0))
    var = tk.BooleanVar(master=win, value=result["stop_service"])
    _cb_cmd = (lambda: on_change(bool(var.get()))) if on_change else None
    tk.Checkbutton(opts, text=checkbox_text, variable=var,
                   command=_cb_cmd).pack(anchor="w")
    btns = tk.Frame(win)
    btns.pack(pady=(8, 12))

    def confirm():
        result.update(go=True, stop_service=bool(var.get()))
        win.destroy()

    def cancel():
        win.destroy()

    quit_btn = tk.Button(btns, text="退出", command=confirm, width=10,
                         bg="#E5534B", fg="#FFFFFF", relief="flat")
    cancel_btn = tk.Button(btns, text="取消", command=cancel, width=10)
    quit_btn.pack(side="left", padx=8)
    cancel_btn.pack(side="left", padx=8)
    cancel_btn.focus_set()
    win.protocol("WM_DELETE_WINDOW", cancel)
    win.bind("<Escape>", lambda _event: cancel())
    win.update_idletasks()
    win.geometry("+%d+%d" % ((win.winfo_screenwidth() - win.winfo_width()) // 2,
                             max(40, (win.winfo_screenheight() - win.winfo_height()) // 3)))
    win.grab_set()
    win.wait_window()
    if _temp_root is not None:
        _temp_root.destroy()
    return result or None


def warn_duplicate_instance(app_name, hint="请看任务栏右下角通知区域里的图标。"):
    """无 console 托盘程序的重复启动提示：print 没人看得见，用弹窗。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            None, f"{app_name} 已经在运行了。\n\n{hint}\n本次启动已取消。", app_name, 0x40)
    except Exception:
        pass


def make_quit_request_path(user_data_dir):
    """--quit 的请求文件：`<app> --quit` 写它，运行中的实例由 quit_watch_loop 消费。

    ⚠ 纪律（F11/D12）：测试与工具链实例必须用重定向后的独立数据区，
    否则会把用户的常驻实例一起退出。
    """
    return user_data_dir / "quit.request"


def quit_watch_loop(stop_event, quit_request_path, on_quit, log=print, beat=1.0):
    """1 秒拍监视退出请求文件；发现即删除并回调 on_quit（走与托盘退出同一条清理路径）。"""
    import time

    while not stop_event.wait(beat):
        try:
            if not quit_request_path.exists():
                continue
            quit_request_path.unlink()
        except OSError as exc:
            log(f"quit request unreadable: {exc}")
            continue
        log("quit requested via --quit")
        on_quit()
        return


def mask_token(url, keep="••••••"):
    """D11：菜单展示用 token 全掩码——知道有 token 但看不到内容；完整地址走「复制面板地址」。"""
    if not url:
        return url
    return re.sub(r"([?&])token=[^&]*", lambda m: m.group(1) + "token=" + keep, url)


class MenuSignature:
    """签名重画：把「会显示出来的状态」提成一个 tuple，只有签名变了才重建菜单句柄，
    菜单开着时推迟（menu_is_open 回调返回 True 时），根治右键菜单开着突然失焦。
    用法：每拍把各项状态喂给 signature()；与上次不同且菜单未开时调 rebuild()。"""

    def __init__(self, rebuild, menu_is_open=lambda: False, log=print):
        self.rebuild = rebuild
        self.menu_is_open = menu_is_open
        self.log = log
        self._last = None
        self._dirty = False

    def update(self, signature):
        if signature == self._last:
            return
        self._last = signature
        if self.menu_is_open():
            self._dirty = True   # 菜单开着：推迟，由 1.5s 补画拍补上
            return
        self._dirty = False
        try:
            self.rebuild()
        except Exception as exc:
            self.log(f"menu rebuild failed: {exc}")

    def flush_deferred(self):
        """menu_refresh_loop（1.5s 拍）调用：菜单关掉后补上被推迟的重画。"""
        if self._dirty and not self.menu_is_open():
            self._dirty = False
            try:
                self.rebuild()
            except Exception as exc:
                self.log(f"deferred menu rebuild failed: {exc}")
