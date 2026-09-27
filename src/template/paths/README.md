# T2 · paths —— 数据区 / 播种 / 稳定安装位

> 规范出处：STANDARDS.md §F1「用户数据区」、§B3 环境变量覆盖；执行文档 F11/D12（实例隔离）。
> 蓝本：local-speak2text/paths.py（reme-helper 同款四区设计）。

## 定位

所有路径的唯一出处：`APP_DIR`（程序本体）、`RUN_DIR`（本次运行的包）、
`USER_DATA_DIR`(用户数据)、`INSTALL_DIR`（稳定安装位）。config / log / update
全部派生自这里，**任何模块禁止自己拼路径**。

## 对外接口（稳定承诺）

| 名称 | 说明 |
|---|---|
| `APP_DIR` / `RUN_DIR` | 打包后 = exe 所在目录；开发态 = 仓库根 |
| `USER_DATA_DIR` | `%LOCALAPPDATA%\<APP_ID>\`；**整体可被 `<APP_ID 大写>_DATA_DIR` env 重定向**（F11：测试/工具链实例必须重定向，严禁与常驻实例共享任何落盘文件） |
| `CONFIG_PATH` | 默认 `USER_DATA_DIR/config.json`；**可被 `<APP_ID 派生式>_CONFIG` 钉死**（1.1.3 补实现，兑现本文档早先承诺，见 CONFORMANCE §4.1.5）。派生式 = `APP_ID.upper().replace('-','_') + '_CONFIG'`（NAME-10），四工具统一按此命名 |
| `LEGACY_CONFIG_PATH` | exe 旁旧位置，仅 `seed_config()` 首次迁移读一次 |
| `LOG_DIR` / `LOG_PATH` | `USER_DATA_DIR/log/`，T12 log_kit 消费 |
| `UPDATE_DIR` | T4 update_helper 的下载/暂存区 |
| `INSTALL_DIR` / `INSTALL_EXE` | 稳定安装位：自启指向这里，更新整目录替换路径不变 |
| `is_stable_install()` | 当前 exe 是否就是稳定位实例 |
| `ensure_user_dirs()` / `seed_config()` | 建目录 / 旧配置一次性播种 |
| `hold_exe_delete_guard()` / `hold_no_delete(p)` | **C-2（1.1.4）**：活实例对自己的 exe 持一个**不含 `FILE_SHARE_DELETE`** 的句柄 ⇒ 删除/改名由**内核**拒绝（本机实测：`unlink` 与 `rmtree(父目录)` = **winerror 32**、`rename(父目录)` = **winerror 5**；无句柄的对照组 `unlink` **成功**）。**`main()` 必须在托盘/窗口创建之前调用一次**并持有到进程结束（故意不 `close`）；**失败必须放行**（D3.2）；**dev 态跳过**（保护 `python.exe` 无意义）。⚠️ "在目录里放 in-use 标记"**只能当检测、不能当防护**——标记就在被盲删的那个目录**内部**，盲删会连它一起删掉 |
| ~~`process_pending_update()`~~ | **⛔ 已弃用（2026-09-19），不得用于新工具。** 它不判 `robocopy` 返回码、失败时**销毁现场**（unlink pending + 删 UPDATE_DIR），已被 `update_helper` 的**稳定安装位模式**取代。现存实现仅为兼容保留，随下一次级联移除；新代码请用 `update_helper` |

## 采纳步骤

1. 拷 `paths.py`，确认 `appconfig.py` 在位（唯一 import）；
2. `main()` 最先调用 `seed_config()`，之后所有模块从这里拿路径；
3. 测试/CI 里设 `<APP>_DATA_DIR` 指向临时目录（实例隔离，F11/D12）；
   需要连配置文件位置也钉死时再设 `<APP>_CONFIG`（1.1.3+）；
4. **`main()` 在托盘/窗口创建之前调用 `hold_exe_delete_guard(log=log)`**（C-2，1.1.4+）——
   顺序不能再往后挪，晚一步就等于那一步的窗口期没有保护。

<!-- MUST-WIRE: hold_exe_delete_guard -->


## 边界与坑

> **⛔ `process_pending_update()` 已弃用（2026-09-19）**：本函数用 `os.system('robocopy … /MIR …')`
> **完全不看返回码**，随后**无条件** `UPDATE_PENDING.unlink()` + `shutil.rmtree(UPDATE_DIR)` ——
> robocopy 失败时它把**暂存源连同失败证据一起销毁**。经审计（ALIGNMENT §7.4）：今天**零在役调用方**
> （l-s2t 用自家加固 fork `updater.py:320`，其余三工具不调用），但它是**随四份副本发货的地雷 +
> 本文档曾承诺的 API**。处置三步：① 本声明（先做）；② 模板落 `update_helper` 稳定安装位模式 →
> l-s2t 改调并删 fork；③ 最后删本函数与 `UPDATE_PENDING` 并做 5 份级联。**②③ 不交叉。**


- exe 旁的文件运行时被锁、更新要整目录替换——**除出厂模板外禁止往 APP_DIR 写状态**。
- 大资源（模型 2GB+）放 APP_DIR 下、不入库，但配置里存相对路径时要配"向上查找回退"。
- 坑实录：reme-helper 独立服务的日志曾落错目录导致 helper 账本失明（F11 一族）——路径必须出自本模块，`v1.2.3` 已改为读 workspace 产物兜底。
