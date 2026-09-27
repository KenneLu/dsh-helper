# service_link —— 服务唯一性、接入与有界清理

> 规范出处：STANDARDS.md **§G4.2**（2026-09-18 新增条款，本模块是其参考实现）。
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
NONE ──start()──────▶ OWNED  ──stop──▶ NONE
OWNED ──进程已消失──▶ NONE（自然收敛）
```

- **NONE**：探测不到服务 → `start()` 允许。
- **ADOPTED**：服务在，但不是本 helper 启动的（helper 打开前就在跑）→ 接入
  pid 登记后固定；停止走优雅 API 优先、按 pid 兜底。
- **OWNED**：本 helper 启动 → 持有句柄；停止按句柄终止整棵进程树。
- 接入存续期间新出现的服务实例：**只登记为旁观者**（业务自由多开，如调试），
  退出时永不清理。

## 对外接口（稳定承诺）

| 成员 | 说明 |
|---|---|
| `refresh()` | 每轮健康检查调用；探测 + 状态自然收敛。返回 `(state, healthy)` |
| `can_start()` / `start()` | 唯一性守卫：仅 `NONE` 态允许启动；`start` 返回 `(ok, msg)` |
| `stop()` | 有界清理：只关接入实例（ADOPTED 优雅优先）；返回 `(ok, msg)` |
| `state` | `none / adopted / owned`——托盘状态行与菜单启停的禁用逻辑都从它派生 |
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
