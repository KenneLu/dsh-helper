# -*- coding: utf-8 -*-
"""测试临时目录清理：统一走模板标准件 ``temp_kit``。

本仓原来自己实现的 `scratch_dir` / `rmtree_cleanup`（含 `H:/Tools/_verify-scratch/` 回落、
自己的 `_LIVE`/`atexit`）已并入模板标准件 `temp_kit` v1.0.0+：

* 路径：``%TEMP%\\<APP_ID>\\<场景>-<时间戳>-<6位随机>\\``（工具名即目录名，归属清晰）；
* 四层清理：退出删 + 退出钩子 + 保留 N=3 + 年龄上限 24h。

本模块只保留**薄封装**，让既有测试的 import 不变；新增测试建议直接
``from template import temp_kit``。
"""
import contextlib
import sys
from pathlib import Path

# template 在 src/ 下；本模块常被测试在把 src 加进 sys.path **之前** import，自己先加。
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from template import temp_kit   # noqa: E402

# 上提到模板的能力，薄封装直接转发（供既有测试 import 不变）
from template.temp_kit import SWEPT, clear_readonly   # noqa: F401


def harden_stdout():
    """让 `print` 在中文 Windows 控制台（GBK）上**永不因编码而崩**。

    真实事故：替换脚本把 `robocopy` 的输出 `>>` 进日志，而 robocopy 在中文系统上
    用 **GBK** 输出本地化错误（"目录名无效"）。测试按 utf-8 读日志 → 不可解字节变成
    `U+FFFD`；再把这段文字打进 GBK 控制台 → `UnicodeEncodeError` 直接**中断测试**，
    于是"断言失败"变成了"测试崩在打印上"，后面的用例全不跑、清理也不跑。
    `errors="replace"` 保编码不变，只把不可编码字符降级为 `?`。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


harden_stdout()


def scratch_dir(prefix):
    """建一个测试临时根（场景名 = prefix 去掉尾随 `-`），返回 **str**（向后兼容）。"""
    scene = (prefix or "scratch").rstrip("-") or "scratch"
    return str(temp_kit.scratch_dir(scene))


def rmtree_cleanup(path, tries=10, delay=0.2):
    """删掉测试临时目录；**返回 True 仅当删后回读确认不存在**。"""
    return temp_kit.rmtree_cleanup(path, tries=tries, delay=delay)


@contextlib.contextmanager
def scratch(prefix):
    """上下文版临时根：退出时删并回读确认（异常退出也删）；yield **str**。"""
    path = scratch_dir(prefix)
    try:
        yield path
    finally:
        rmtree_cleanup(path)
