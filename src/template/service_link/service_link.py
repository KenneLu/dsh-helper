# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/service_link/service_link.py | TEMPLATE-VER: 0.2.0
# 0.2.0：**启动编排三件 + 就绪钩子**——①STARTING 态（NONE→STARTING→OWNED，
#   launch 抛异常回滚 NONE——旧版 launch 失败会卡在半启动态）；②wait_ready(timeout,
#   abort_event)（对齐 reme wait_for_health：STOP_EVENT 立断、健康轮询、超时如实报）；
#   ③ensure_running()（probe 命中→ADOPTED 收养；否则 start+wait_ready——「服务就绪后
#   起隧道」的编排入口）；④on_ready 回调（wait_ready 成功即调，衔接 tunnel_kit）。
#   README 补 abort_event 装配规约（shutdown 竞态，reme main.py:2826-2828 教训）。
"""服务接入与唯一性（helper ↔ 服务 的所有权模型）。

四条铁律：
  唯一性   —— helper 的"启动"只在探测不到服务时允许执行；探测到即拒绝。
  外部自由 —— helper 之外手动多开服务实例属于业务自由，不阻止、不清理、不刷屏。
  接入     —— 以规范端点的实际应答者为唯一接入对象；接入 pid 登记后固定不漂移。
  有界清理 —— 停止/退出的作用域 = 接入实例本身；ADOPTED 优雅 API 优先、其次按
              pid 终止；**严禁全量签名击杀**。

状态机（自然收敛，无外力不迁移）：
  NONE ──探测到服务──▶ ADOPTED ──stop──▶ NONE
  NONE ──start──────▶ STARTING ──launch 成功──▶ OWNED ──stop──▶ NONE
                        │                          
                        └─launch 抛异常──▶ NONE（回滚，不留半启动态）
  OWNED ──进程消失──▶ NONE（自己起的死了，状态回零）
  ADOPTED 存续期间出现的新实例：登记为旁观者（spectators），不清理。

依赖：纯标准库。psutil/process 枚举通过回调注入（adopt_pid / terminate），
让本模块保持零依赖、可单测。
"""

# 状态常量
STATE_NONE = "none"        # 未发现服务
STATE_STARTING = "starting"  # launch 已发、就绪未确认（0.2.0）：wait_ready 的窗口态
STATE_ADOPTED = "adopted"  # 识别接入：服务不是本 helper 启动的
STATE_OWNED = "owned"      # 本 helper 启动并持有句柄（就绪已确认或调用方不等就绪）

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
        """唯一性守卫下的启动。返回 (ok, msg)。非 NONE 态一律拒绝。

        0.2.0：先入 STARTING 再 launch——launch 抛异常时回滚 NONE（旧版直接把
        异常抛给调用方、状态已不在 NONE，后续 start 会被"already running"拒绝，
        一个启动失败的服务把状态机卡死）。成功即 OWNED；要不要等就绪由调用方
        决定（wait_ready / ensure_running）。
        """
        if self.state != STATE_NONE:
            self.log("start refused: service already running")
            return False, "service already running"
        self.state = STATE_STARTING
        try:
            self.handle = self.launch()
        except Exception as exc:
            self.state, self.handle = STATE_NONE, None
            self.log(f"launch failed, rolled back to none: {exc}")
            return False, f"launch failed: {exc}"
        self.state = STATE_OWNED
        return True, "started"

    def wait_ready(self, timeout, abort_event=None, interval=0.5):
        """轮询 probe 至健康。返回 (ok, msg)。

        对齐 reme wait_for_health 语义：
          * abort_event 一旦置位**立即**返回（False, "aborted"）——shutdown 竞态下
            退出必须比超时快（reme main.py:2826-2828 教训：等满超时才退会让用户
            看着托盘"卡住关不掉"）。abort 只中断等待，**不动进程**——已 launch 的
            服务留在 OWNED，由调用方决定 stop 与否（退出路径本来就整链收）。
          * 超时如实报（False, "timeout after Ns"），同样不动进程。
          * interval 是轮询间隔；probe 异常按不健康处理（fail-open 不适用这里：
            等待侧保守即多等一轮，refresh 侧的 fail-open 语义不受影响）。
        """
        import time as _time
        deadline = _time.monotonic() + max(0.0, timeout)
        while True:
            if abort_event is not None and abort_event.is_set():
                return False, "aborted"
            try:
                if self.probe():
                    self._fire_on_ready()
                    return True, "ready"
            except Exception as exc:
                self.log(f"probe error during wait_ready (treated as not ready): {exc}")
            if _time.monotonic() >= deadline:
                return False, f"timeout after {timeout}s"
            _time.sleep(min(interval, max(0.0, deadline - _time.monotonic())))

    def ensure_running(self, timeout=60.0, abort_event=None, interval=0.5):
        """服务确保在跑：probe 命中→收养（ADOPTED）；否则启动并等就绪。

        「启动工具→拉服务→就绪后起隧道」的编排入口：一次调用完成
        adopt-or-start + ready 确认，on_ready 回调在就绪瞬间触发。
        """
        state, healthy = self.refresh()
        if healthy:
            self._fire_on_ready()
            return True, f"adopted (state={state})"
        ok, msg = self.start()
        if not ok:
            return False, msg
        return self.wait_ready(timeout, abort_event=abort_event, interval=interval)

    def on_ready(self, callback):
        """注册就绪回调（wait_ready/ensure_running 探测成功的瞬间调用，异常不外抛）。

        典型用途：服务就绪后逐 ssh 目标 ensure 隧道（tunnel_kit 衔接点）。
        """
        self._on_ready_cb = callback

    def _fire_on_ready(self):
        cb = getattr(self, "_on_ready_cb", None)
        if cb is None:
            return
        try:
            cb()
        except Exception as exc:  # noqa: BLE001 - 编排回调失败不该打断就绪判定
            self.log(f"on_ready callback failed: {exc}")

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
