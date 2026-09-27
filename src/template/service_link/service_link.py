# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/service_link/service_link.py | TEMPLATE-VER: 0.1.0
"""服务接入与唯一性（helper ↔ 服务 的所有权模型）。

规范出处：STANDARDS.md §G4.2。四条铁律：
  唯一性   —— helper 的"启动"只在探测不到服务时允许执行；探测到即拒绝。
  外部自由 —— helper 之外手动多开服务实例属于业务自由，不阻止、不清理、不刷屏。
  接入     —— 以规范端点的实际应答者为唯一接入对象；接入 pid 登记后固定不漂移。
  有界清理 —— 停止/退出的作用域 = 接入实例本身；ADOPTED 优雅 API 优先、其次按
              pid 终止；**严禁全量签名击杀**。

状态机（自然收敛，无外力不迁移）：
  NONE ──探测到服务──▶ ADOPTED ──stop──▶ NONE
  NONE ──start──────▶ OWNED  ──stop──▶ NONE
  OWNED ──进程消失──▶ NONE（自己起的死了，状态回零）
  ADOPTED 存续期间出现的新实例：登记为旁观者（spectators），不清理。

依赖：纯标准库。psutil/process 枚举通过回调注入（adopt_pid / terminate），
让本模块保持零依赖、可单测。
"""

# 状态常量
STATE_NONE = "none"        # 未发现服务
STATE_ADOPTED = "adopted"  # 识别接入：服务不是本 helper 启动的
STATE_OWNED = "owned"      # 本 helper 启动并持有句柄


class ServiceLink:
    """helper 与目标服务之间的唯一绑定。

    回调契约（由工具注入，本模块不 import 任何工具代码）：
      probe()        -> bool        规范端点健康探测（如 GET /health_check）
      launch()       -> handle      启动服务进程，返回句柄（如 Popen）；仅在 NONE 态被调用
      terminate(h)   -> None        有界终止：OWNED 传句柄（终止进程树），ADOPTED 传 pid
      graceful()     -> bool|None   可选；ADOPTED 优雅关闭 API（返回 True=已关）
      adopt_pid()    -> pid|None    可选；多候选时选出规范端点的应答者（无则不记 pid）
      log(*a)        -> None        日志出口
    """

    def __init__(self, *, probe, launch, terminate, graceful=None,
                 adopt_pid=None, log=print):
        self.probe = probe
        self.launch = launch
        self.terminate = terminate
        self.graceful = graceful
        self.adopt_pid = adopt_pid
        self.log = log
        self.state = STATE_NONE
        self.handle = None       # OWNED: Popen 句柄；ADOPTED: pid
        self.spectators = []     # 存续期间发现的其他实例 pid：只登记，永不清理

    # ---------- 状态收敛 ----------

    def refresh(self):
        """每轮健康检查调用：探测 + 状态自然收敛。返回 (state, healthy)。"""
        try:
            healthy = bool(self.probe())
        except Exception:
            healthy = False
        if healthy and self.state == STATE_NONE:
            pid = None
            if self.adopt_pid is not None:
                try:
                    pid = self.adopt_pid()
                except Exception as exc:
                    self.log(f"adopt pid lookup failed: {exc}")
            self.state, self.handle = STATE_ADOPTED, pid
            self.log(f"service adopted (pid={pid})")
        elif not healthy and self.state == STATE_OWNED and self.handle is not None:
            exited = getattr(self.handle, "poll", lambda: None)() is not None
            if exited:
                # 自己启动的进程已经消失：状态自然收敛，不给托盘留僵尸"运行中"
                self.state, self.handle = STATE_NONE, None
        return self.state, healthy

    # ---------- 启动（唯一性守卫） ----------

    def can_start(self) -> bool:
        return self.state == STATE_NONE

    def start(self):
        """唯一性守卫下的启动。返回 (ok, msg)。非 NONE 态一律拒绝。"""
        if self.state != STATE_NONE:
            self.log("start refused: service already running")
            return False, "service already running"
        self.handle = self.launch()
        self.state = STATE_OWNED
        return True, "started"

    # ---------- 有界清理 ----------

    def stop(self):
        """只关闭**接入实例**。返回 (ok, msg)。

        ADOPTED：优雅关闭 API 优先（服务自己收尾最干净），失败再按接入 pid 终止。
        OWNED：直接按句柄终止进程树。
        spectators 永不清理——它们是业务自由多开的实例。
        """
        if self.state == STATE_NONE:
            return True, "service not running"
        if self.state == STATE_ADOPTED and self.graceful is not None:
            try:
                if self.graceful():
                    self.log("service stopped via graceful API")
                    self.state, self.handle = STATE_NONE, None
                    return True, "stopped gracefully"
            except Exception as exc:
                self.log(f"graceful shutdown failed: {exc}")
        try:
            self.terminate(self.handle)
        except Exception as exc:
            self.log(f"terminate failed: {exc}")
            return False, f"terminate failed: {exc}"
        self.state, self.handle = STATE_NONE, None
        return True, "stopped"

    def note_spectators(self, pids):
        """登记探测发现但未接入的其他实例（只记录，永不清理）。"""
        known = set(self.spectators) | {self.handle if isinstance(self.handle, int) else None}
        for pid in pids:
            if pid is not None and pid not in known:
                self.spectators.append(pid)
                self.log(f"spectator service instance noted (pid={pid})")
