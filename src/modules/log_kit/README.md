# T12 · log_kit —— 滚动日志 + 打开日志目录

> 规范出处：STANDARDS.md §D4（D13：参数统一 1MB×3）；执行文档 F12。
> 蓝本：reme-helper 的 RotatingFileHandler 方案（实测 ~4MB 封顶）。

## 定位

运行日志的唯一写入口。两个硬规矩：① 日志失败**静默**——日志永远不能弄死主流程；
② 托盘必有「打开日志目录」项（D13 对标杆 reme-helper 的补强：它自己反而藏在设置里）。

## 对外接口（稳定承诺）

| 名称 | 说明 |
|---|---|
| `make_logger(log_dir)` | 返回 `(log, open_log_dir)` 两个闭包；`log(msg)` 写一行 INFO，`open_log_dir()` 起 explorer |
| `get_logger(log_dir)` | 底层：`RotatingFileHandler(maxBytes=1MB, backupCount=3, utf-8)`，幂等 |
| `LOG_MAX_BYTES` / `LOG_BACKUPS` | 统一参数，改这里 = 改全家族（需升 TEMPLATE-VER 并三轮复审） |

## 采纳步骤

1. 拷 `log_kit.py`（依赖 `appconfig.APP_ID`）；
2. 初始化：`log, open_log_dir = log_kit.make_logger(LOG_DIR)`；
3. 菜单「打开日志目录」→ `open_log_dir`；启动/退出/关键动作各记一行。

## 边界与坑

- 崩溃兜底（`crash.log` 全量 traceback）与运行日志是**两份**：崩溃文件要能独立于日志子系统存活（`--noconsole` 打包后 stderr 不存在）；
- **禁止**密钥进日志；多实例共享同一日志文件 = 事故（F11），实例隔离见 T2；
- 日志是 helper 类工具的"账本"——但账本消费者要挑权威来源（reme-helper 的教训：日志可能缺失，workspace 产物才是权威）。
