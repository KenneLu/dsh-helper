# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/log_kit/log_kit.py | TEMPLATE-VER: 1.0.2
"""T12｜运行日志：RotatingFileHandler 单文件 1MB、保留 3 个滚存（总量 ~4MB 封顶）。

参数与 reme-helper 的日志方案一致（house 标准 D13）：日志跟数据区走
%LOCALAPPDATA%\\<工具名>\\log\\，托盘「打开日志目录」一键到达。
任何日志失败都静默——日志永远不能把主流程弄死。
"""
import os

from modules.appconfig import APP_ID

LOG_MAX_BYTES = 1 << 20      # 1 MB per file
LOG_BACKUPS = 3              # <app>.log.1 ... .3

_logger = None


def get_logger(log_dir):
    global _logger
    if _logger is None:
        import logging
        from logging.handlers import RotatingFileHandler

        log_dir.mkdir(parents=True, exist_ok=True)
        lg = logging.getLogger(APP_ID)
        if not lg.handlers:
            try:
                handler = RotatingFileHandler(
                    log_dir / (APP_ID + ".log"),
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUPS,
                    encoding="utf-8",
                )
            except Exception:
                # 升级窗口期另一实例还开着日志文件时改名会失败（Windows）：
                # 宁可这一轮不滚，也不要因为日志装不上而启动失败（reme-helper 语义，1.0.2 沉淀）
                handler = logging.FileHandler(
                    log_dir / (APP_ID + ".log"), encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
            lg.propagate = False
        _logger = lg
    return _logger


def make_logger(log_dir):
    """返回 (log, open_log_dir) 两个闭包；工具初始化时调用一次。"""
    logger = get_logger(log_dir)

    def log(message):
        try:
            logger.info(message)
        except Exception:
            pass

    def open_log_dir():
        log_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(log_dir))  # noqa: S606

    return log, open_log_dir
