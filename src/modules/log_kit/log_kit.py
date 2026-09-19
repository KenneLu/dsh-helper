# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/modules/log_kit/log_kit.py | TEMPLATE-VER: 1.0.3
# 1.0.3：`log` 改 **print 形态**（`def log(*parts)`，空格拼接 `str(part)`）。
#   根因是**契约冲突**：`update_helper`（同批派发件）里 7 处按 print 形态调用
#   （`log("downloading", stem)` / `log("update staged:", a, "->", b, "(bat %s)" % s)`），
#   而这里给的是单参闭包 → 传进来的那一刻就在**每条真路径上** TypeError
#   （L407 每次下载 / L257 每次拉起替换脚本 / L390 有失败 marker 时）。
#   为什么以前没炸：dsh/ocx 各自在工具侧写了 `def log(*parts)` 兜住（且都留了
#   "模板 log_kit 待统一"的注释）；l-s2t 的包装更窄（`def log(message)`），
#   **却照样 `log=log` 交给了模板件** → 真缺陷（已实测：传进 launch_pending_cmd /
#   pop_failed_update_note 时，模板件 L257 / L390 直接 TypeError）。
#   **冲突被三份互不相同的本地包装分别盖住 / 藏住**，模板层面于是长期保持
#   "两个派发件说不同的话"。统一到 print 形态：与 `update_helper` / `tray_kit` 的
#   调用形态一致，也与 `print` 直觉一致；工具侧的本地包装可以退化成直接转发或删除。
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
    """返回 (log, open_log_dir) 两个闭包；工具初始化时调用一次。

    `log` 是 **print 形态**：接受任意个位置参数，用空格拼接（1.0.3 起的稳定承诺）。
    `update_helper` / `tray_kit` 都按这个形态调用；C-29 会机械检查工具传给模板件的
    日志函数能否接受可变参数。
    """
    logger = get_logger(log_dir)

    def log(*parts):
        try:
            logger.info(" ".join(str(part) for part in parts))
        except Exception:
            pass

    def open_log_dir():
        log_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(log_dir))  # noqa: S606

    return log, open_log_dir
