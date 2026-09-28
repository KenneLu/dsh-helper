# tunnel_kit — ssh 反向隧道统一件（T8，0.1.0，W7）

蓝本：opencodex-helper 已验证隧道段；术语裁定（用户）：一律「**ssh 目标**」。

## API

| 符号 | 语义 |
|---|---|
| `TunnelTarget(target, *, probe, spawn, matches, params, log)` | 单目标生命周期。`target={"host","port","remote_port","local_host","local_port"}`；`spawn(args)` 拉起（密钥/密码型由消费方定可执行）；`matches(cmdline)` 收养扫描谓词 |
| `build_reverse_args(target, *, alive=None)` | 纯函数：`-N -o ServerAliveInterval/CountMax -o ExitOnForwardFailure=yes -p <port> -R <rport>:<lhost>:<lport> <host>`（Decision 10 参数化） |
| `ensure()` | 幂等：活着不动（OWNED/ADOPTED）；连续 `confirm_n` 次失败确认死亡 → 按 30s→10min 退避重连 |
| `start()` / `stop_owned_only()` | 拉起 / **只终止 OWNED 句柄** |
| `monitor_once(on_reconnect=)` | 单轮监控钩子（重连成功回调，衔接图标/菜单刷新） |

## 三层探测

① 本机服务健康（service_link 管，`on_ready` 后起隧道）→ ② 隧道进程存活
（`proc.poll()`；收养态 tasklist+命令行特征）→ ③ 远端转发链路（注入的 `probe`）。

## 红线（杀进程纪律）

只按 **OWNED 句柄**终止；收养扫描**只按命令行特征**（`-N -R` + user@host），
**严禁按进程名 ssh 统杀**——用户自有 ssh（SOCKS `-D`）与家族隧道同名共存。

## Decision 10 默认参数（`DEFAULTS`，appconfig 可覆盖）

`confirm_n=3`（连续失败确认）、`backoff_start_s=30 → backoff_max_s=600`、
`ServerAliveInterval=30 / ServerAliveCountMax=3`、`ExitOnForwardFailure=yes`。
