# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/tray_kit/tray_kit.py | TEMPLATE-VER: 2.2.0
"""T7｜托盘机制件：单实例互斥体、退出请求文件 + 监视循环、面板地址行掩码、菜单签名重画、退出确认框（2.0.0）。

2.2.0：**合法性判据单一化**——新增 `mutex_name_ok(name)`（纯字符串判定：非空字符串、
以 Local 命名空间前缀起头、前缀之后不得再出现反斜杠），**守卫与探针共用同一段**
（此前守卫不校验、探针只问内核，等于两份口径）；`single_instance_free` 同样接入。
守卫遇非法名**放行**并记日志（D3.2），把"打红"交给构建期的探针（D3.3）。

2.1.0：新增 **`mutex_name_is_valid(app_id, mutex_name=None)`** —— 冒烟用的**守卫覆盖探针**
（名字合法性，不占锁、不弹窗）。来源：local-speak2text 内联版（其"守卫坏了 3 个月而构建全绿"
的根因修复），按 D3.1 提升为公共件，让四工具一次性解决 C-10。

2.0.2：`confirm_quit_dialog` / `warn_duplicate_instance` 的用户可见文案参数化
（中文为默认值，向后兼容），满足 E4-02「词表覆盖全部用户可见文案」；
`checkbox_text` 为空/None 时不再渲染空勾选框。

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


def _derive_mutex_name(app_id, mutex_name=None):
    """实际要用的互斥体名（**单一派生口径**，守卫与探针都从这里取）。"""
    return mutex_name if mutex_name is not None else f"Local\\{app_id}-single-instance"


def mutex_name_ok(name):
    """名字**形状**是否合法——纯字符串判定，不碰内核。**守卫与探针共用这一段**。

    规则（SINGLE-01 的根因，全家族只此一份判据）：
      * 必须是**非空字符串**（`None` / `""` / 非 `str` 一律不合法）；
      * 必须以 `Local\\` 起头；
      * 前缀之后**不得再出现反斜杠**——命名内核对象只允许一个分隔符，第二个会让
        `CreateMutexW` 直接失败（err=3），而旧守卫把"创建失败"当成"已有实例"
        ⇒ 双击 exe 永远弹"已在运行"、工具打不开，且构建全绿。
    """
    if not isinstance(name, str) or not name.startswith("Local\\"):
        return False
    rest = name[len("Local\\"):]
    return bool(rest) and "\\" not in rest


def acquire_single_instance(app_id, mutex_name=None, log=print):
    """命名互斥体钉死进程数为 1。返回 False = 已有实例。守卫自身失败时放行。"""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    name = _derive_mutex_name(app_id, mutex_name)
    if not mutex_name_ok(name):
        # 非法名是编程错误，但在用户机器上"打不开"比"少一层保护"严重得多 ⇒ 放行（D3.2）。
        # 构建期由 --smoke 的探针把它打红（D3.3），不靠运行时兜。
        log(f"single-instance guard: illegal mutex name {name!r}; continuing WITHOUT the guard")
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)
        handle = k32.CreateMutexW(None, False, name)
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
    否则退出前用户紧接着启动的托盘会以为自己被抢了。名字非法时同样放行（失败方向）。
    """
    if os.name != "nt":
        return True
    if not mutex_name_ok(mutex_name):
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    handle = k32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return True
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    k32.CloseHandle(handle)
    return not already


def mutex_name_is_valid(app_id, mutex_name=None):
    """探针：这个名字**形状合法且内核收得下**（`--smoke` 的守卫覆盖用）。返回 True/False。

    两步，与守卫**同源**：
      ① `mutex_name_ok()` —— 纯字符串判据，守卫用的是同一段（不写两份规则）；
      ② 内核探测 —— `CreateMutexW` 后立刻 `CloseHandle`：**不占锁、不弹窗**。

    与 `single_instance_free()` 的区别（两者都不持有锁，问的问题不同）：
      * `single_instance_free` 问"此刻别处有没有实例在跑"——用户常驻实例在跑时它是 False，
        拿它做冒烟断言会**假红**；
      * 本函数问"**这个名字能成立吗**"——`ERROR_ALREADY_EXISTS` **也算合法**（名字被占用
        恰恰证明内核接受了它），故与"有没有实例在跑"无关。

    为什么冒烟要用探针而不是真跑守卫（D3.3）：真跑守卫遇到用户常驻实例会走"重复启动"
    分支 → `warn_duplicate_instance()` 弹**模态**对话框 → **无人值守的构建被挂死**
    （比失败更糟：CI 卡住而不是变红）。而冒烟真正要打的故障是**名字非法**（SINGLE-01）。

    失败方向与守卫一致：非 Windows 或内核不可用 ⇒ **放行**（宁可漏判，不可把工具判死）。
    """
    if os.name != "nt":
        return True
    name = _derive_mutex_name(app_id, mutex_name)
    if not mutex_name_ok(name):
        return False       # 形状非法 ⇒ 不必问内核，直接判不合法
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)   # 防上一次的 183 被误读（l-s2t 实测教训）
        handle = k32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if handle:
            k32.CloseHandle(handle)  # 只探测，不持有
        return bool(handle) and err in (0, ERROR_ALREADY_EXISTS)
    except Exception:
        return True


def confirm_quit_dialog(app_name, checkbox_text=None, checked_init=False,
                        parent=None, on_change=None, *, title=None, body_text=None,
                        confirm_text="退出", cancel_text="取消"):
    """退出确认 + 清理勾选对话框（G4.1 条款 4 / G4.2 条款 5；交互形态 = reme-helper 蓝本）。

    形态（家族标准，勿各自发挥）：标题 = title 或 app_name；正文默认「确定退出
    <app_name>？勾选项会记住，下次退出沿用。」（无勾选项时为「确定退出 <app_name>？」）；
    checkbox_text 非空才渲染单个 Checkbutton；退出钮红底 #E5534B flat 在左、
    取消 width=10 在右并持默认焦点；屏幕垂直 1/3 居中；模态 grab_set；
    Esc/关窗 = 取消（不退出）。

    parent：常驻 UI 线程的工具传 tk 父窗口；托盘菜单线程场景传 None（内部建临时
    Tk 根，wait_window 后销毁——对话框生命周期完全属于调用线程）。
    on_change(bool)：勾选状态一变即回调（2026-09-18 用户定：持久化跟随勾选动作，
    不等「退出」点击——点取消也已留存）。调用方在此落盘。

    2.0.2（E4-02）：本函数是纯机制件，**不得硬编码用户可见文案**——启用 i18n 的工具
    经 title/body_text/confirm_text/cancel_text 传入 t() 词条；不传则用中文默认值，
    老调用点行为不变。checkbox_text 为空/None 时不再渲染空勾选框（l-s2t 形态）。
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
    win.title(title or app_name)
    win.attributes("-topmost", True)
    win.resizable(False, False)
    has_checkbox = bool(checkbox_text)
    result = {"go": False, "stop_service": bool(checked_init) and has_checkbox}

    body = tk.Frame(win)
    body.pack(padx=18, pady=(14, 6))
    if body_text is None:
        body_text = (f"确定退出 {app_name}？勾选项会记住，下次退出沿用。"
                     if has_checkbox else f"确定退出 {app_name}？")
    tk.Label(body, text=body_text, justify="left",
             wraplength=380).pack(anchor="w")
    var = None
    if has_checkbox:
        opts = tk.Frame(win)
        opts.pack(anchor="w", padx=18, pady=(6, 0))
        var = tk.BooleanVar(master=win, value=result["stop_service"])
        _cb_cmd = (lambda: on_change(bool(var.get()))) if on_change else None
        tk.Checkbutton(opts, text=checkbox_text, variable=var,
                       command=_cb_cmd).pack(anchor="w")
    btns = tk.Frame(win)
    btns.pack(pady=(8, 12))

    def confirm():
        result.update(go=True,
                      stop_service=bool(var.get()) if var is not None else False)
        win.destroy()

    def cancel():
        win.destroy()

    quit_btn = tk.Button(btns, text=confirm_text, command=confirm, width=10,
                         bg="#E5534B", fg="#FFFFFF", relief="flat")
    cancel_btn = tk.Button(btns, text=cancel_text, command=cancel, width=10)
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


def warn_duplicate_instance(app_name, hint="请看任务栏右下角通知区域里的图标。",
                            message=None, title=None):
    """无 console 托盘程序的重复启动提示：print 没人看得见，用弹窗。

    2.0.2（E4-02）：文案可整体经 message/title 传入（i18n 工具传 t() 词条）；
    不传则用中文默认值，老调用点行为不变。
    """
    text = message if message is not None else (
        f"{app_name} 已经在运行了。\n\n{hint}\n本次启动已取消。")
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title or app_name, 0x40)
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
