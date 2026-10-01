# tunnel_kit — ssh 反向隧道统一件（0.1.1，W7）

蓝本：opencodex-helper 已验证隧道段；术语裁定（用户）：一律「**ssh 目标**」。
（编号口径 F-R5：无 T 编号——T8 属 build_release，模块 T 号 T1–T12 已满，同 service_link 无号先例。）

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

## 最低消费口径：「退避翻倍」腿（F-R4，W8-A 登记；W8-C 更新）

指数退避的**翻倍腿只活在 `TunnelTarget.ensure()`**（`backoff_s` 每次失败重连后 ×2、
封顶 `backoff_max_s`、重连成功即复位到起始值）。**消费方若只取
`build_reverse_args`/`DEFAULTS` 而不用 `TunnelTarget`，翻倍腿不生效**。休眠名单
更新（09-29）：**ocx 已在自建循环实现完整序列（N1 修复：30s 起步→失败 ×2→600s
封顶→成功/探测恢复复位）**；**reme 仍为休眠形态**（自持续命监控无翻倍，N2 降级
留痕——升级迁移 `TunnelTarget` 已于 10-01 复审 F-R11 正式改期至 **reme 下次
大版本**，原「10-01 排期」到期未兑现）。最低消费要求：自建监控循环的
消费方必须自行实现「30s 起步 → 每次失败 ×2 → 600s 封顶 → 成功复位」的完整序列
（或迁移到 `TunnelTarget`）；只取起始值不翻倍 = 未达 D10 口径。

## probe 异常方向（#45，N3 于 0.1.2 对齐）

`alive()` 的 probe 回调**抛异常 = fail-open**（保持现态不触发重连；NONE 态仍
False）——「探测跑不成 ≠ 不健康」。0.1.1 前为 fail-closed（仪器故障会被当链路
死亡触发无谓重连），消费方 ocx 本地 `probe_target` 一直是对的（fail-open），模板
0.1.2 起与蓝本一致。
