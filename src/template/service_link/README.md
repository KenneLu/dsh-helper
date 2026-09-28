# service_link —— 服务唯一性、接入与有界清理

> 规范出处：家族规范.md **§G4.2**（2026-09-18 新增条款，本模块是其参考实现）。
> 蓝本：reme-helper 的 `service_up / matching_reme_processes / SERVICE_PROCESS` 语义
> （探测优先 + managed 区分）+ dsh-helper 的"接管已运行服务"设计；
> 历史反例（已修）：opencodex-helper 早期用"退出签名击杀"关闭服务
> （G4.2 条款 3 的反面教材）；现改为**勾选制、默认不勾**，退出不再主动击杀。

## 定位

helper 类工具与它托管的服务之间的**所有权状态机**。回答三个问题：
现在服务在吗（探测）？我能再起一个吗（唯一性守卫）？退出时我负责关掉谁（有界清理）？

## 状态机

```
NONE ──探测到服务────▶ ADOPTED ──stop──▶ NONE
NONE ──start()──────▶ STARTING ──launch 成功──▶ OWNED ──stop──▶ NONE
                       └─launch 抛异常──▶ NONE（回滚，不留半启动态）
OWNED ──进程已消失──▶ NONE（自然收敛）
```

- **NONE**：探测不到服务 → `start()` 允许。
- **STARTING**（0.2.0）：`start()` 先入此态再 launch；launch 抛异常回滚 NONE
  （旧版失败会把状态机卡在半启动态，后续 start 被"already running"拒绝）。
- **ADOPTED**：服务在，但不是本 helper 启动的（helper 打开前就在跑）→ 接入
  pid 登记后固定；停止走优雅 API 优先、按 pid 兜底。
- **OWNED**：本 helper 启动 → 持有句柄；停止按句柄终止整棵进程树。
- 接入存续期间新出现的服务实例：**只登记为旁观者**（业务自由多开，如调试），
  退出时永不清理。

## 对外接口（稳定承诺）

| 成员 | 说明 |
|---|---|
| `refresh()` | 每轮健康检查调用；探测 + 状态自然收敛。返回 `(state, healthy)` |
| `can_start()` / `start()` | 唯一性守卫：仅 `NONE` 态允许启动；`start` 先入 STARTING 再 launch，异常回滚 NONE。返回 `(ok, msg)` |
| `wait_ready(timeout, abort_event=None, interval=0.5)` | 0.2.0：轮询 probe 至健康/超时/abort；就绪触发 on_ready。abort 立断且**不动进程** |
| `ensure_running(timeout=60.0, …)` | 0.2.0：probe 命中→收养 ADOPTED；否则 start+wait_ready。「服务就绪后起隧道」的编排入口 |
| `on_ready(callback)` | 0.2.0：就绪回调（异常不外抛）；tunnel_kit 的衔接点 |
| `stop()` | 有界清理：只关接入实例（ADOPTED 优雅优先）；返回 `(ok, msg)` |
| `state` | `none / starting / adopted / owned`——托盘状态行与菜单启停的禁用逻辑都从它派生 |
| `spectators` | 发现但未接入的实例 pid（只登记，永不清理） |

## 采纳步骤（回调注入，模块零依赖）

1. 拷 `service_link.py`；
2. 注入四个回调：
   - `probe`：规范端点健康探测（如 `GET /health_check`）；
   - `launch`：启动服务进程（subprocess），返回句柄；
   - `terminate`：按句柄/pid 终止进程树；
   - `graceful`（可选）：服务优雅关闭 API（如 `POST /shutdown`）；
   - `adopt_pid`（可选）：多个候选进程时选出规范端点的应答者；
3. 托盘"启动"菜单 enabled 绑 `can_start()`，动作走 `start()`；
4. 退出确认的"顺便关闭服务"勾选（持久化、默认不勾）→ 勾了才调 `stop()`；
5. 停止动作对接管实例同样可用——`stop()` 内部已按 ADOPTED/OWNED 分流。

## 边界与坑

- **严禁全量签名击杀**：按命令行特征 psutil 扫全场会把业务自由多开的实例一起误杀
  （G4.2 条款 3 反例）；候选筛选只用于**选出一个**接入 pid；
- 外部服务的日志落点可能不同——helper 的统计账本不要只依赖日志（reme-helper v1.2.3
  教训：workspace 产物才是权威）；
- `--quit`/外部退出导致 helper 被杀时无清理机会，服务越过托盘继续运行 = 预期行为
  （G4.2 条款 4），helper 重启后自动重新接入。


## 0.2.0：启动编排三件 + 就绪钩子（W7）

| API | 语义 |
|---|---|
| `start()` | 先入 STARTING 再 launch；launch 抛异常回滚 NONE（不留半启动态卡死状态机） |
| `wait_ready(timeout, abort_event=None, interval=0.5)` | 轮询 probe 至健康/超时/abort。abort **立即**返回且不动进程（已 launch 的留 OWNED，由调用方处置） |
| `ensure_running(timeout, ...)` | probe 命中→收养 ADOPTED；否则 start+wait_ready。「启动工具→拉服务→就绪后起隧道」的编排入口 |
| `on_ready(callback)` | 就绪瞬间回调（异常不外抛）；tunnel_kit 的衔接点 |

### abort_event 装配规约（shutdown 竞态，reme main.py:2826-2828 教训）

**必须**把主程序的 STOP_EVENT 传给 `abort_event`：退出路径上等待必须比超时先断，
否则用户看着托盘"卡住关不掉"。abort 只中断等待、不杀进程——退出链路本就整链收尾，
服务归属 OWNED/ADOPTED 由既有 stop 语义处理。

**适用边界（豁免条款）**：上条"必须"只约束**持有 STOP_EVENT、存在退出等待**的工具——
无 STOP_EVENT 的 daemon-boot 编排（启动即交付、无常驻退出路径，如 boot 期一次性
`ensure_running`）**不适用**本规约：`abort_event` 可不传（默认 `None`），等待上界由
`timeout` 自身兜底；豁免只免"装配义务"，abort 语义本身不变（仍只断等待、不动进程，
已 launch 的留 OWNED 由调用方处置）。
