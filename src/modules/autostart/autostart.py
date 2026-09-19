# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/autostart/autostart.py | TEMPLATE-VER: 1.1.3
# 1.1.3（任务 #63）：`migrate_autostart` 的不动条件从「目标存在」收紧为
#   「**值 == 当前命令行** 且 目标存在」—— **存在性谓词不能代理"当前性"**：
#   本家族 `release/` 保留历史版本目录（回滚路径），所以"旧版 exe 还在"恒真，
#   旧实现对"指向废弃版本"的自启项**永不自愈**。实例：dsh 的 Run 键曾指着
#   `release\dsh-helper-1.8.2\`（目录仍在）⇒ 登录拉起旧版、占住单实例互斥体、
#   用户双击新版反而提示"已有实例"。同批覆盖 §4.1.22 那半（值相等但目标没了）。
"""T3｜开机自启三件套（蓝本 local-speak2text，规范 G4.1 认定的更优形态）。

get_autostart_cmd 优先指向稳定安装位 INSTALL_EXE（路径永不因更新改变）；
migrate_autostart 启动自愈：注册表指向的 exe 已消失时重写到当前值。

1.1.2（2026-09-19）：修 migrate_autostart 两个真缺陷 —— ①"条目不存在"与"读失败"
都被 `except: pass` 吞成静默（不新建是对的，**不出声不是**）；②判据 `value == wanted`
短路 + `value.strip('"')` 判存在（对源码态命令行永不成立）→ 死链漏修与无谓重写并存。
详见函数 docstring 与 `_cmd_targets`。
"""
import os
import re
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


_QUOTED = re.compile(r'"([^"]*)"')


def _cmd_targets(cmd):
    """从 Run 键存的**整条命令行**里取出全部路径 token。

    注册值有两种形态：`"C:\\...\\app.exe"`（打包态，1 个 token）与
    `"C:\\...\\pythonw.exe" "C:\\...\\main.py"`（源码态，2 个 token）。

    旧实现用 `value.strip('"')` 判存在：对打包态成立，对源码态得到
    `...pythonw.exe" "...main.py` 这种**不可能存在**的路径 —— 于是只要注册命令行
    与当前命令行有一丁点不同（入口脚本改名/挪位/换路径大小写），就每次都判
    "目标已消失"并白写一次注册表。这里按命令行规则取所有**带引号**的 token
    （没有引号时取第一个空白分隔 token）。

    口径：`get_autostart_cmd()` 自己给每个路径都加引号，所以引号内的就是路径；
    手改过的无引号值会被判成"需要重写"，而我们重写出来的正是带引号的规范形态——
    这是修复而不是破坏。
    """
    quoted = _QUOTED.findall(cmd)
    if quoted:
        return quoted
    return cmd.strip().split()[:1]


def _cmd_alive(cmd):
    """整条命令行的**每一个**路径 token 都存在，才算"这个自启项活着"。

    只看第一个 token 会漏掉源码态的"解释器还在、入口脚本没了"——那时 Run 项
    看起来是指向一个存在文件的，实则开机什么都不会发生。
    """
    targets = _cmd_targets(cmd)
    return bool(targets) and all(os.path.exists(t) for t in targets)


def migrate_autostart(log=print):
    """自启键指向的 exe 若已不存在（旧版本目录被删），重写到当前命令行。

    两条语义都是 2026-09-19 修的真缺陷（模板 1.1.1 及三份副本）：

    1. **不新建，但要出声**：条目不存在（用户自己关掉了自启，或被杀软清掉）时
       **不自动创建** —— 那等于替用户把他关掉的自启重新打开。但旧实现把
       `FileNotFoundError` 与真正的读失败一起吞进 `except: pass`，于是"本来就没
       这条"和"读不出来"在日志里**完全同形**（都是什么都不打印），现场无法区分。
       现在两种情况各记一行，且**不写注册表**。
    2. **判据只看"目标是否存在"**：旧实现是
       `value != wanted and not os.path.exists(...)`，`value == wanted` 会**短路**
       —— 注册值恰好等于会写进去的值、而目标已不在时不做任何事。现在去掉这一半；
       重写后**复查**新目标，仍不存在就如实报告，不谎报 `migrated`。
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
    except FileNotFoundError:
        log("autostart migrate: %s has no Run entry - not creating one" % APP_NAME)
        return
    except OSError as e:
        log("autostart migrate: cannot read Run entry: %s" % e)
        return

    # T13（任务 #63）：**存在性谓词不能代理"当前性"**。
    # 本家族 `release/` 保留历史版本目录（回滚路径），所以"旧版 exe 还在"恒真
    # ⇒ `_cmd_alive(value)` 对**指向废弃版本**的自启项恒真 ⇒ 永不自愈。
    # 实例：dsh 的 Run 键曾指着 `release\dsh-helper-1.8.2\`（目录仍在），
    # 登录拉起旧版、占住单实例互斥体，用户双击新版反而提示"已有实例"。
    # ⇒ 不动条件收紧为「**值就是当前命令行** 且 它活着」；任一不满足就重写：
    #   · 值 != 当前 ⇒ 指向旧版本目录（本条的由来）
    #   · 值 == 当前但目标没了 ⇒ §4.1.22 那半（旧实现的 `value == wanted` 短路）
    wanted = get_autostart_cmd()
    if value == wanted and _cmd_alive(value):
        return

    try:
        set_autostart(True)
    except OSError as e:
        log("autostart migrate: rewrite failed: %s" % e)
        return
    if _cmd_alive(wanted):
        log("autostart migrated: %s -> %s" % (value, wanted))
    else:
        log("autostart migrate: %s is gone and its replacement %s is missing too"
            % (value, wanted))
