# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/tunnel_kit/tunnel_kit.py | TEMPLATE-VER: 0.1.2
# 0.1.2：alive() 的 probe 异常改 fail-open（蓝本语义对齐：探测跑不成
#   ≠不健康，保持现态不触发重连；原 fail-closed 会把仪器故障当链路死亡）。
# 0.1.1：build_reverse_args 加 remote_bind 可选——远端只听
#   127.0.0.1 的收紧形态（reme 既有语义，模板化收编）。
# 0.1.0：统一 ssh 隧道模块——以 opencodex-helper 现有件为底抽象。
#   术语裁定（用户）：一律「ssh 目标」，不用「VM 目标」。
#
"""ssh 反向隧道统一件：启动/探测/自愈/有界停止。

蓝本：opencodex-helper 已验证的隧道段（幂等 start_target / probe_target /
OWNED 句柄唯一认领），吸收 reme 的服务编排语义（service_link.on_ready 衔接）。

## 三层探测（健康判定从便宜到贵）
  ① 本机服务健康 —— 由 service_link 负责（本模块不重复实现；on_ready 后起隧道）
  ② 隧道进程存活 —— proc.poll()（OWNED 句柄）或命令行特征扫描（收养态）
  ③ 远端转发链路 —— 消费方注入的 probe（如经隧道打本机回环端点）

## 自愈（全部参数可调，默认值即裁定值）
  * 确认次数 N=3：连续 N 次探测失败才判死（防单次抖动触发重连）
  * 退避 30s→10min：每次重连后失败确认的等待翻倍增长，封顶 10 分钟
  * ssh 参数：ServerAliveInterval=30 / ServerAliveCountMax=3（半开连接内核级检测）
    + ExitOnForwardFailure=yes（转发口被占即退，不僵尸）
  * 新实例自动拉起：判死后由监控循环重连（ensure 语义）

## 杀进程纪律（红线）
  **只按 OWNED 句柄终止**；收养/扫描场景**只按命令行特征匹配**（本模块签名的
  `-N -R <port>` 组合 + 目标 user@host），**严禁按进程名 "ssh" 统杀**——用户自有
  ssh（如 SOCKS -D）与本家族隧道同名共存。

## 认证
  密钥型：系统 ssh.exe + 私钥（默认 ssh 代理链）。
  密码型：消费方注入 auth 段（蓝本 ocx 的 plink 路线；本模块不内置 plink，
  经 launch 注入命令行构造器）。
"""
import shlex
import subprocess
import time

# 默认参数（消费方可在 appconfig 覆盖后传入）
DEFAULTS = {
    "confirm_n": 3,            # 连续失败确认次数
    "backoff_start_s": 30.0,   # 首次重连退避
    "backoff_max_s": 600.0,    # 退避封顶（10min）
    "server_alive_interval": 30,
    "server_alive_count_max": 3,
}

# 状态常量（与 service_link 同型语义：OWNED=自己起的；ADOPTED=收养既有进程）
STATE_NONE = "none"
STATE_OWNED = "owned"
STATE_ADOPTED = "adopted"

def build_reverse_args(target, *, alive=None, remote_bind=None):
    """构造反向隧道命令行参数段（纯函数）。

    target: {"host": "user@host", "port": 22, "remote_port": 10100,
             "local_host": "127.0.0.1", "local_port": 10100}
    alive: DEFAULTS 的子集。
    remote_bind: 远端监听绑定地址（None=远端全听；"127.0.0.1"=只在远端本机听——
                 reme 形态，更收紧）。
    返回 list[str]（不含 ssh 可执行路径——密钥/密码型的可执行与认证段由消费方拼）。
    """
    a = dict(DEFAULTS, **(alive or {}))
    fwd = f"{target['remote_port']}:{target.get('local_host', '127.0.0.1')}:{target['local_port']}"
    if remote_bind:
        fwd = f"{remote_bind}:{fwd}"
    return [
        "-N",
        "-o", f"ServerAliveInterval={a['server_alive_interval']}",
        "-o", f"ServerAliveCountMax={a['server_alive_count_max']}",
        "-o", "ExitOnForwardFailure=yes",
        "-p", str(target.get("port", 22)),
        "-R", fwd,
        target["host"],
    ]

def _signature(target):
    """目标特征串（收养扫描/判重用）：-R 端口 + host。"""
    return (str(target["remote_port"]), str(target["host"]))

class TunnelTarget:
    """单个 ssh 目标的隧道生命周期（幂等 start / 三层探测 / 自愈确认计数）。

    回调契约（消费方注入）：
      probe()          -> bool   远端转发链路探测（③层；None=跳过该层）
      spawn(args)      -> Popen  真正拉起（密钥型 ssh.exe / 密码型 plink 由这里定）
      matches cmdline  -> bool   进程收养扫描谓词（命令行含本目标特征）
      log(*a)          -> None
    """

    def __init__(self, target, *, probe=None, spawn, matches=None, params=None, log=print):
        self.target = dict(target)
        self.probe_cb = probe
        self.spawn = spawn
        self.matches = matches or (lambda cmdline: False)
        self.params = dict(DEFAULTS, **(params or {}))
        self.log = log
        self.state = STATE_NONE
        self.proc = None            # OWNED 句柄
        self.fail_streak = 0        # 连续失败计数（确认用）
        self.backoff_s = self.params["backoff_start_s"]
        self.last_action = 0.0      # 上次 start/重连时刻（退避计算）

    # ---------- 探测 ----------

    def alive(self):
        """②+③ 层探测：进程存活 + （有 probe 时）转发链路。返回 bool。"""
        if self.state == STATE_OWNED and self.proc is not None:
            if self.proc.poll() is not None:
                return False        # 进程已退（ExitOnForwardFailure 等原因）
        elif self.state == STATE_ADOPTED:
            if not self._adopted_alive():
                return False
        if self.probe_cb is not None:
            try:
                return bool(self.probe_cb())
            except Exception as exc:  # noqa: BLE001
                # fail-open（0.1.2 修复）：探测跑不成 ≠ 不健康——
                # 保持现态、不触发重连；NONE 态本无链路可保，仍 False。
                self.log(f"tunnel probe error (fail-open, keep {self.state}): {exc}")
                return self.state in (STATE_OWNED, STATE_ADOPTED)
        return self.state in (STATE_OWNED, STATE_ADOPTED)

    def _adopted_alive(self):
        """收养态进程存活：任务表扫描 + 命令行特征（消费方注入 matches）。"""
        try:
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except Exception as exc:  # noqa: BLE001
            self.log(f"tasklist scan failed (fail-open, skip heal): {exc}")
            return True             # 扫描失败不触发重连（探测失败≠不健康的 fail-open 边界）
        for line in out.splitlines():
            if line and self.matches(line):
                return True
        return False

    # ---------- 生命周期 ----------

    def ensure(self):
        """幂等：活着不动（OWNED/ADOPTED）；死了按退避重连。返回 (ok, msg)。"""
        if self.alive():
            self.fail_streak = 0
            self.backoff_s = self.params["backoff_start_s"]
            if self.state == STATE_NONE:
                # 既有进程收养（扫描命中）
                self.state = STATE_ADOPTED
                return True, "adopted"
            return True, f"alive ({self.state})"
        self.fail_streak += 1
        if self.fail_streak < self.params["confirm_n"]:
            return False, f"unconfirmed ({self.fail_streak}/{self.params['confirm_n']})"
        # 确认死亡 → 退避节流的重连
        now = time.monotonic()
        if now - self.last_action < self.backoff_s:
            return False, f"backoff ({self.backoff_s:.0f}s since last start)"
        return self.start()

    def start(self):
        """拉起隧道（OWNED）。参数进命令行（build_reverse_args）。"""
        self.stop_owned_only()
        args = build_reverse_args(self.target, alive=self.params)
        try:
            self.proc = self.spawn(args)
        except Exception as exc:  # noqa: BLE001
            self.state, self.proc = STATE_NONE, None
            self.last_action = time.monotonic()
            self.backoff_s = min(self.backoff_s * 2, self.params["backoff_max_s"])
            self.log(f"tunnel start failed ({self.target['host']}): {exc}")
            return False, f"start failed: {exc}"
        self.state = STATE_OWNED
        self.fail_streak = 0
        self.last_action = time.monotonic()
        self.log(f"tunnel up: {self.target['host']} -R {self.target['remote_port']}")
        return True, "started"

    def stop_owned_only(self):
        """只终止 OWNED 句柄（红线：收养态与无关 ssh 进程永不碰）。"""
        if self.state == STATE_OWNED and self.proc is not None:
            try:
                self.proc.terminate()
            except Exception as exc:  # noqa: BLE001
                self.log(f"tunnel terminate error (ignored): {exc}")
        self.proc = None
        if self.state != STATE_ADOPTED:
            self.state = STATE_NONE

    # ---------- 自愈监控 ----------

    def monitor_once(self, *, on_reconnect=None):
        """单轮监控（消费方的循环里调）：确认死亡后重连，成功回调。返回 (ok, msg)。"""
        ok, msg = self.ensure()
        if ok and on_reconnect is not None and "started" in msg:
            try:
                on_reconnect()
            except Exception as exc:  # noqa: BLE001
                self.log(f"on_reconnect failed: {exc}")
        return ok, msg
