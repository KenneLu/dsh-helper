# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/paths/paths.py | TEMPLATE-VER: 1.1.1
"""T2｜路径与数据区（蓝本 local-speak2text/paths.py）。

四个位置，职责分明：APP_DIR 程序本体；RUN_DIR 本次运行的包；USER_DATA_DIR 用户
数据（config + log + update）；INSTALL_DIR 稳定安装位（自启指向，更新不变）。
**数据根整体可被环境变量重定向**（F11 教训）：测试/工具链必须用独立数据区，
严禁与用户常驻实例共享 config/log/退出请求等任何落盘文件。
"""
import os
import shutil
import sys
from pathlib import Path

from modules.appconfig import APP_ID

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
RUN_DIR = APP_DIR

_DATA_ROOT = Path(
    os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
)
# F11：整个数据根可重定向——测试实例设 <APP_ID>_DATA_DIR 指向临时目录即可与生产完全隔离
USER_DATA_DIR = Path(os.environ.get(f"{APP_ID.upper().replace('-', '_')}_DATA_DIR") or _DATA_ROOT) / APP_ID

CONFIG_PATH = USER_DATA_DIR / "config.json"
LEGACY_CONFIG_PATH = APP_DIR / "config.json"   # 旧位置（exe 旁），仅播种时读一次

LOG_DIR = USER_DATA_DIR / "log"
UPDATE_DIR = USER_DATA_DIR / "update"
UPDATE_PENDING = RUN_DIR / "update.pending.json"
LOG_PATH = LOG_DIR / (APP_ID + ".log")
UPDATE_DIR = USER_DATA_DIR / "update"

INSTALL_DIR = USER_DATA_DIR / "app"
INSTALL_EXE = INSTALL_DIR / f"{APP_ID}.exe"


def is_stable_install():
    """当前 exe 是否就是稳定安装位里的那个（更新器安装的正式实例）。"""
    try:
        return Path(sys.executable).resolve() == INSTALL_EXE.resolve()
    except OSError:
        return False


def ensure_user_dirs():
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def seed_config():
    """首次运行（数据区还没有 config.json）时，从旧位置迁移一份作初始配置。"""
    ensure_user_dirs()
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    try:
        if LEGACY_CONFIG_PATH.exists():
            shutil.copyfile(LEGACY_CONFIG_PATH, CONFIG_PATH)
    except OSError:
        pass
    return CONFIG_PATH


def process_pending_update():
    """（可选，T4 配套）启动兜底：处理上次会话退出时未完成的更新镜像任务。"""
    if not UPDATE_PENDING.exists():
        return False
    try:
        import json
        info = json.loads(UPDATE_PENDING.read_text(encoding="utf-8"))
        staged, target = Path(info["staged"]), Path(info["target"])
        if staged.is_dir() and target.is_dir():
            os.system(f'robocopy "{staged}" "{target}" /MIR /R:1 /W:1 /NFL /NDL /NP >nul')
        UPDATE_PENDING.unlink(missing_ok=True)
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)
        return True
    except Exception:
        return False
