# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/paths/paths.py | TEMPLATE-VER: 1.1.4
"""T2｜路径与数据区（蓝本 local-speak2text/paths.py）。

四个位置，职责分明：APP_DIR 程序本体；RUN_DIR 本次运行的包；USER_DATA_DIR 用户
数据（config + log + update）；INSTALL_DIR 稳定安装位（自启指向，更新不变）。
**数据根整体可被环境变量重定向**（F11 教训）：测试/工具链必须用独立数据区，
严禁与用户常驻实例共享 config/log/退出请求等任何落盘文件。

1.1.2：dev 态锚定改为「向上查找 main.py 所在目录的上一级（仓库根）」——家族统一
src/main.py + src/modules/ 布局后，本文件不再依赖自身所在深度。
1.1.3：补 `<APP_ID 派生>_CONFIG` 环境变量（CONFIG_PATH 可被显式钉死）——兑现
README 早已承诺的接口，消除「模板相对蓝本功能回退」（CONFORMANCE §4.1.5）。
1.1.4：新增 **C-2** `hold_no_delete()` / `hold_exe_delete_guard()`——活实例对自己的
exe 持一个**不含 `FILE_SHARE_DELETE`** 的句柄，把"别删正在运行的实例目录"从**纪律**
升级成**内核强制**（删除方大声失败 winerror 32，而不是静默掏空目录）。**必须由
`main()` 在托盘/窗口创建之前调用**（README 有 MUST-WIRE 声明，C-27 会打红没接线的工具）。
"""
import os
import shutil
import sys
from pathlib import Path

from modules.appconfig import APP_ID

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    # dev 态：exe 旁语义 = 仓库根（出厂 config/模型/构建产物住根）；src/ 只放代码。
    # 向上找 main.py 所在的 src/，再上一级 = 仓库根——与本模块所在深度无关。
    _here = Path(__file__).resolve()
    _src_dir = next((p for p in _here.parents if (p / "main.py").exists()), _here.parents[1])
    APP_DIR = _src_dir.parent
RUN_DIR = APP_DIR

_DATA_ROOT = Path(
    os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
)
# 环境变量前缀：连字符转下划线（B3/NAME-10，如 dsh-helper -> DSH_HELPER）
_ENV_PREFIX = APP_ID.upper().replace("-", "_")
# F11：整个数据根可重定向——测试实例设 <APP_ID 派生>_DATA_DIR 指向临时目录即可与生产完全隔离
USER_DATA_DIR = Path(os.environ.get(f"{_ENV_PREFIX}_DATA_DIR") or _DATA_ROOT) / APP_ID

# 1.1.3：配置文件位置可被 <APP_ID 派生>_CONFIG 显式钉死（测试/便携；l-s2t 蓝本同款语义）
_CONFIG_OVERRIDE = os.environ.get(f"{_ENV_PREFIX}_CONFIG")
CONFIG_PATH = (Path(_CONFIG_OVERRIDE).expanduser() if _CONFIG_OVERRIDE
               else USER_DATA_DIR / "config.json")
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


_NO_DELETE_HANDLE = None   # 活实例对自己 exe 的"拒绝删除"句柄；**故意不关**：寿命 = 进程寿命


def hold_no_delete(path):
    """以**不含 `FILE_SHARE_DELETE`** 的方式打开 `path`，成功返回内核句柄，失败返回 `None`。

    C-2 的原语："活实例不可被删除"由**内核**保证，不由纪律保证。持有它时
    `unlink` / `rmtree(<父目录>)` / `rename(<父目录>)` 全部失败——helpers-dev 本机实测：
    `unlink` = winerror 32、`rmtree(release 目录)` 同、`rename(父目录)` = winerror 5；
    无句柄的对照组 `unlink` 成功。

    **为什么值得有**：删除方（构建脚本 / 手工 `rm -r` / 未来的 `--clean`）会**大声失败**，
    而不是"静默把正在运行的实例目录掏空"——后者正是 2026-09-19 那次事故的形态
    （实例仍在其中运行时，`release\\<工具>-<版本>\\` 被掏空）。
    """
    try:
        import ctypes
        GENERIC_READ = 0x80000000
        FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2     # 故意**不给** FILE_SHARE_DELETE
        OPEN_EXISTING = 3
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        h = k32.CreateFileW(str(path), GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if not h or h == ctypes.c_void_p(-1).value:
            return None
        return h
    except Exception:
        return None


def hold_exe_delete_guard(log=print):
    """启动早期调用一次：让**本实例的 exe** 在被删除/改名时由内核拒绝（C-2）。返回是否持有。

    **硬要求**（lead 2026-09-19 裁定）：
      * **进程启动早期调用，且在托盘/窗口创建之前**——晚了就等于没保护（窗口期仍可被删）；
      * 句柄**持有到进程结束**（故意不 `close`：寿命就是进程寿命，不需要 `finally`）；
      * **失败必须放行**（D3.2）：拿不到句柄只记一行日志，绝不拦住启动；
      * **dev 态（未冻结）不取**：那时"本实例"是 `python.exe`，保护它没有意义，
        反而会让开发机的 Python 升级莫名失败。

    ⚠️ 与"在目录里放 in-use 标记"的分工：**标记只能当检测，不能当防护**——标记就在被盲删的
    那个目录**内部**，盲删会连它一起删掉。防护只能来自目录**之外**的东西（本句柄，或一个
    不看目录内容的删除守卫）。
    """
    global _NO_DELETE_HANDLE
    if _NO_DELETE_HANDLE is not None:
        return True
    if not getattr(sys, "frozen", False):
        log("exe delete-guard: dev mode - skipped (a handle on python.exe protects nothing)")
        return False
    h = hold_no_delete(sys.executable)
    if h is None:
        log("exe delete-guard: cannot take a handle - continuing WITHOUT it (D3.2 fail-open)")
        return False
    _NO_DELETE_HANDLE = h
    return True


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
