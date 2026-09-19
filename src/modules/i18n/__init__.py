# TEMPLATE-MODULE: i18n | TEMPLATE-VER: 2.1.1
"""i18n 包门面：**不复制状态**（2.1.1）。

`from .i18n import *` 会把 `LANG` 这类**可变全局拷成静态副本**：`init('en')` 之后
子模块真值已变，包命名空间里却还是导入时那份 'zh'。任何从包读 `i18n.LANG` 的调用点
都会拿到过期值（实例：菜单签名算出来不变 → 切了语言菜单却不重建）。

做法：
  * 只把**函数**绑进包命名空间（函数调用时读子模块全局，无副本问题）；
  * 其余属性（`LANG` / `TABLES` / `KEY_MIN_SET` …）交给 **PEP 562 模块级
    `__getattr__`**，每次读取都委派到子模块真值。

⚠️ 因此 `LANG` **绝不能**出现在下面的 import 里——`__getattr__` 只在常规查找失败时
触发；一旦把 `LANG` 绑进包命名空间，就又变成死副本，本机制立刻失效。
消费方请优先用 `current_lang()`（推荐读法）。
"""
from . import i18n  # 子模块：唯一真值来源（也支撑 `from modules.i18n import i18n`）
from .i18n import (  # 函数按引用绑定：调用时读子模块全局，不复制状态
    available_langs,
    current_lang,
    detect_system_lang,
    init,
    load_language_from_config,
    load_tables,
    save_language_to_config,
    t,
)

__all__ = [
    "i18n", "available_langs", "current_lang", "detect_system_lang", "init",
    "load_language_from_config", "load_tables", "save_language_to_config", "t",
]


def __getattr__(name):
    """PEP 562：包命名空间查不到的名字（`LANG`/`TABLES`/…）每次读取取子模块真值。"""
    try:
        return getattr(i18n, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None
