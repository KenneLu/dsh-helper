# -*- coding: utf-8 -*-
"""测试临时目录清理：先放句柄、再删、删后回读；删不掉就返回 False（由测试变红）。

为什么需要它（实测，不是推测）：
  `%TEMP%` 里积了 一批 `dsh-helper` 残留，签名高度一致——残留物只有
  `<TMP>/<app>/log/<app>.log`，有时再加一个刚写完的 `config.json`。
  两个成因都成立：
    ① `log_kit` 的 `RotatingFileHandler` 持有日志文件；Windows 下 Python 的
       `open()` **不带 FILE_SHARE_DELETE**，被打开的文件删不掉；
    ② 刚写完的 `config.json` 会被 Defender / 索引器短暂占用（瞬时锁，重试即过）。
  `shutil.rmtree(..., ignore_errors=True)` 把这两种"删不动"都变成"看起来成功"，
  于是泄漏无声积累（每跑一轮门禁就多几个目录）。本模块把"删干净"变成可断言的结论。

用法（在每个建了 `mkdtemp` 的测试末尾）：
    check("temp dir cleaned up (no %TEMP% leak)", rmtree_cleanup(_TMP), str(_TMP))
"""
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# R2（2026-09-19 lead 裁定）：测试/探针的临时目录一律放 `H:\Tools\_verify-scratch\`，
# 不得在系统 `%TEMP%` 里留家族前缀的目录。可用 DSH_HELPER_SCRATCH_DIR 覆盖（CI 用）。
_SCRATCH_ENV = "DSH_HELPER_SCRATCH_DIR"
_DEFAULT_SCRATCH = r"H:\Tools\_verify-scratch"


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


# 四个测试都 import 本模块；在这里统一生效，免得有人忘了调。
harden_stdout()


def scratch_dir(prefix):
    """建一个测试临时根：优先 `H:\\Tools\\_verify-scratch\\`，不可用才回落 `%TEMP%`。

    回落也必须能被 `rmtree_cleanup` 删干净——泄漏判据与位置无关。
    """
    base = os.environ.get(_SCRATCH_ENV) or _DEFAULT_SCRATCH
    try:
        p = Path(base)
        p.mkdir(parents=True, exist_ok=True)
        return tempfile.mkdtemp(prefix=prefix, dir=str(p))
    except Exception:
        return tempfile.mkdtemp(prefix=prefix)


def _drop_log_handlers():
    """close 并摘掉所有 logging handler——释放 RotatingFileHandler 占住的日志文件。

    只 `logging.shutdown()` 不够：它 close 掉 handler 但仍留在 logger.handlers 里，
    且 log_kit 惰性缓存的 logger 对象还在；这里两件事都做，幂等。
    """
    for name in list(logging.Logger.manager.loggerDict):
        try:
            lg = logging.getLogger(name)
        except Exception:
            continue
        for h in lg.handlers[:]:
            try:
                h.close()
            except Exception:
                pass
            try:
                lg.removeHandler(h)
            except Exception:
                pass
    try:
        logging.shutdown()
    except Exception:
        pass


def rmtree_cleanup(path, tries=10, delay=0.2):
    """删掉测试临时目录；**返回 True 仅当删后回读确认不存在**。

    先放句柄再删；对瞬时锁重试；每次重试前再放一次句柄（句柄可能在删除过程中
    被重新建立——log_kit 的 `get_logger` 是惰性 + 模块级缓存）。
    """
    path = str(path)
    for _ in range(max(1, tries)):
        _drop_log_handlers()
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(delay)
    return not os.path.exists(path)
