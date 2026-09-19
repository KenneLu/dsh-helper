# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/autostart/autostart.py | TEMPLATE-VER: 1.1.1
"""T3｜开机自启三件套（蓝本 local-speak2text，规范 G4.1 认定的更优形态）。

get_autostart_cmd 优先指向稳定安装位 INSTALL_EXE（路径永不因更新改变）；
migrate_autostart 启动自愈：注册表指向的 exe 已消失时重写到当前值。
"""
import os
import sys
import winreg

from modules.appconfig import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_autostart_cmd(target="stable"):
    """target: "stable"=稳定安装位形态（local-speak2text 蓝本）；
    "runtime"=运行目录形态（dsh/opencodex，无稳定位概念）。行为差异只此一处。"""
    if getattr(sys, "frozen", False):
        if target == "runtime":
            return '"%s"' % os.path.abspath(sys.executable)
        from modules.paths import INSTALL_EXE, is_stable_install

        if is_stable_install() or INSTALL_EXE.exists():
            return '"%s"' % str(INSTALL_EXE)
        return '"%s"' % os.path.abspath(sys.executable)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.abspath(sys.argv[0])
    if os.path.exists(pythonw):
        return '"%s" "%s"' % (pythonw, script)
    return '"%s" "%s"' % (sys.executable, script)


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, get_autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def migrate_autostart(log=print):
    """自启键指向的 exe 若已不存在（旧版本目录被删），重写到当前命令行。"""
    try:
        current = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
        wanted = '"%s"' % current
        if value != wanted and not os.path.exists(value.strip('"')):
            set_autostart(True)
            log("autostart migrated: %s -> %s" % (value, wanted))
    except (FileNotFoundError, OSError):
        pass
