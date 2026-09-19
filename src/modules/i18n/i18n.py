# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/i18n/i18n.py | TEMPLATE-VER: 2.2.0
"""T5 · i18n v2 —— 机制与词表分离（数据驱动，蓝本 local-speak2text/i18n.py）。

代码只管机制（回退/格式化/持久化/探测）；词条是**数据**：工具根目录
`locales/zh.json` + `locales/en.json`（扁平 KV，utf-8）。工具加词条只改 JSON，
本文件与模板永远归一化一致（执行文档 N2/M1）。zh 键为基准，en 缺失回退 zh，
永不 KeyError。内置最小兜底表：locales 缺失时机制仍可用。

2.1.0：locales 目录解析内置 + 模块导入即自动加载——工具侧零样板，import 即得词条。
目录解析用**向上查找回退**（平铺与 src/modules 布局都命中），打包态回退 _MEIPASS。
2.2.0：`LANG` 从"模块级标量 + `global` 重绑"改为 `_STATE` 容器 + `__getattr__` **只读派生**
（与 `update_helper` 1.4.0 同一手法）。2.1.1 只做到"门面委派对了"，里层仍是**脆形态**——
C-23 把四工具全命中正说明它是模板层面的形态问题。

2.1.1：语言状态改由**访问器** `current_lang()` 暴露（`LANG` 降为内部实现）。
原因：包 `__init__.py` 若 `from .i18n import *`，会把 `LANG` 拷成**静态副本**，
于是 `i18n.init('en')` 之后从包读到仍是 'zh'，而 `t()` 已切到英文——读到过期语言
的调用点（如菜单签名计算）会判定"无需重建"，出现"通知是英文、菜单还是中文"。
消费方请读 `current_lang()`；`LANG` 仅保留兼容，勿在外部读取/写入。
"""
import json
import os
import sys
from pathlib import Path

_BUILTIN_ZH = {"menu_quit": "退出", "menu_open_logs": "打开日志目录"}
_BUILTIN_EN = {"menu_quit": "Quit", "menu_open_logs": "Open log folder"}

TABLES = {"zh": dict(_BUILTIN_ZH), "en": dict(_BUILTIN_EN)}

# 「对外可见状态」的唯一写入点（STANDARDS §D6）。2.2.0 起 `LANG` **不再是可被 global
# 重绑的模块级标量**，而是由本模块的 `__getattr__` 现算的只读派生值——于是
# `from .i18n import *` **拿不到它**（`import *` 不搬运派生名），旧缺陷在结构上无法复现：
# 那个缺陷正是"模块级标量 + 函数内 global + 包 import * 读死副本"，害过一次（菜单不刷新）。
_STATE = {"lang": "zh"}


def __getattr__(name):
    """PEP 562：`LANG` 由 `_STATE` 派生（读得到、永远活值、模块里没有该全局）。"""
    if name == "LANG":
        return _STATE["lang"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
# 门禁 2（D1）断言的核心键集：词表必须能回答这些键
KEY_MIN_SET = ("menu_quit", "menu_open_logs")


def load_tables(locales_dir):
    """从 locales_dir 合并 zh.json / en.json（数据文件，随工具分发）。失败静默。"""
    for lang, filename in (("zh", "zh.json"), ("en", "en.json")):
        path = os.path.join(str(locales_dir), filename)
        try:
            with open(path, encoding="utf-8") as f:
                TABLES[lang].update(json.load(f))
        except (OSError, ValueError):
            pass


def detect_system_lang():
    try:
        import ctypes
        return "zh" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF == 0x04 else "en"
    except Exception:
        return "zh"


def init(language="auto"):
    if language == "auto":
        language = detect_system_lang()
    _STATE["lang"] = language if language in TABLES else "zh"


def current_lang():
    """当前语言（**推荐给消费者的唯一读法**；2.1.1 新增）。

    状态经访问器暴露，不作为可变全局被外部读取——这样包门面无论怎么导入，
    读到的都是子模块真值，而不是 `from .i18n import *` 拷出的死副本。
    """
    return _STATE["lang"]


def t(key, *args, **kwargs):
    """取词。支持 %s 与 {name} 两种填充。"""
    text = TABLES.get(_STATE["lang"], {}).get(key) or _BUILTIN_ZH.get(key) or key
    if args and "%" in text:
        text = text % args
    if kwargs:
        text = text.format(**kwargs)
    return text


def available_langs():
    return sorted(TABLES.keys())


def load_language_from_config(config_path):
    try:
        with open(config_path, encoding="utf-8-sig") as f:
            return str(json.load(f).get("language", "auto"))
    except Exception:
        return "auto"


def save_language_to_config(config_path, language):
    try:
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
        cfg["language"] = language
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _locales_dir():
    """locales 数据目录：从本文件位置逐级向上查找（平铺与 src/modules 布局都命中）；
    打包态回退 _MEIPASS（PyInstaller --add-data 落点）。找不到时退回最后一级
    （load_tables 对缺失文件静默，兜底表保证机制可用）。"""
    here = Path(__file__).resolve().parent
    for base in (here, *here.parents):
        cand = base / "locales"
        if cand.is_dir():
            return cand
    meipass = getattr(sys, "_MEIPASS", "")
    return Path(meipass) / "locales" if meipass else here / "locales"


load_tables(_locales_dir())
