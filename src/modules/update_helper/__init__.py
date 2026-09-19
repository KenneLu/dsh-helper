# TEMPLATE-MODULE: update_helper | TEMPLATE-VER: 1.2.0
"""update_helper 包门面：**不复制状态**（1.1.0 起）。

`from .update_helper import *` 会把 `UPDATE_READY` / `PENDING_CMD` 拷成**静态副本**：
子模块里 `global PENDING_CMD` 重绑的是**子模块那份**，而工具读 `update_helper.PENDING_CMD`
读的是**包命名空间里导入时定格的 None** → `apply.cmd` 永不拉起、更新装了等于没装，
**且无任何异常**。i18n 2.1.1 是同一根因、同一形状的第一例。

做法（同 i18n 2.1.1）：
  * 只把**函数**绑进包命名空间（函数调用时读子模块全局，无副本问题）；
  * 其余属性（`UPDATE_READY` / `PENDING_CMD` / `REPO` / `CHECK_INTERVAL` …）交给
    **PEP 562 模块级 `__getattr__`**，每次读取都委派到子模块真值；
  * 推荐消费方式：`update_ready()` / `pending_cmd()` 访问器。

⚠️ 因此 `UPDATE_READY` / `PENDING_CMD` **绝不能**出现在下面的 import 里——`__getattr__`
只在常规查找失败时触发；一旦绑进包命名空间，就又变成死副本，本机制立刻失效。
"""
from . import update_helper  # 子模块：唯一真值来源（也支撑 `from modules.update_helper import update_helper`）
from .update_helper import (  # 函数按引用绑定：调用时读子模块全局，不复制状态
    build_apply_script,
    check_update,
    download_and_prepare,
    http_error_hint,
    pending_cmd,
    sweep_stale_update_dirs,
    update_ready,
)

__all__ = [
    "update_helper", "build_apply_script", "check_update", "download_and_prepare",
    "http_error_hint", "pending_cmd", "sweep_stale_update_dirs", "update_ready",
]


def __getattr__(name):
    """PEP 562：包命名空间查不到的名字（`UPDATE_READY`/`PENDING_CMD`/…）每次读取取真值。"""
    try:
        return getattr(update_helper, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None
