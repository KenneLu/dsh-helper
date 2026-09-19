# T4 · update_helper —— 在线更新三段式（查 → 下 → 换）

> 规范出处：STANDARDS.md §G4/G4.1、§H 发布链规范 6（zip 约定）、§B4（**公共件冲突以
> reme-helper 的已验证实现为基准**）。
> 基准实现：reme-helper 的查-下-换链路（`src/main.py` 7098-7345，真实发过版、跑过替换）；
> 另参考 local-speak2text/updater.py（稳定位形态）与 dsh/opencodex（运行目录形态）。
> **本模块是"接口化"的旗舰案例**：dsh-helper 与 opencodex-helper 的副本与模板正文逐字节一致，
> 差异全部收敛进各自的 `appconfig.py`（1.3.0 之前如此；1.3.0 起两边需重拷 `modules/update_helper/` 整夹）。

## 定位

三段式：**查**（GitHub Releases latest，内存 24h 节流）→ **下**（zip + `.sha256` 校验，
**缺校验文件也中止**）→ **换**（写一次性替换脚本：等本进程退出 → 快照 → `robocopy /e /purge`
铺目标目录 → **检查 rc**，失败回铺快照 → 重启 → 自删）。为什么换目录必须独立脚本：Windows
上运行中的 exe 换不掉。

## 对外接口（稳定承诺）

| 函数/全局 | 说明 |
|---|---|
| `check_update(current_version, force=False)` | 返回 `dict(latest, current, newer, error)`；失败写 `error` 不抛（带配额人话）。**成功时顺带更新 `UPDATE_READY`**（1.1.0 起） |
| `download_and_prepare(latest, target_dir, update_dir, log=…, backup_dir=None, snapshot_dir=None)` | 下载+校验+暂存+生成替换脚本；成功返回脚本路径并置 `PENDING_CMD`。sha256 不匹配**或缺失**即抛 `RuntimeError` |
| **`update_ready()`** | 有新版时的版本号，否则 `None`（**推荐读法**，1.1.0）。"下载并更新"菜单可用性据此判断 |
| **`pending_cmd()`** | 已就绪的脚本路径，否则 `None`（**推荐读法**，1.1.0）。托盘退出后由此拉起 |
| **`pop_failed_update_note(update_dir, log=…)`** | 启动时读一次上次失败 marker，返回人话（无则空串）并删除（1.3.0）。读到就通知用户 |
| **`sweep_stale_update_dirs(max_age=3600)`** | 清 %TEMP% 里被中断的更新暂存（只清一小时前的），返回个数（1.2.0） |
| **`verify_zip_sha256(zip_path, sha_text)`** | 纯函数校验，返回 `(ok, 人话)`；**期望值为空也算失败**（1.3.0） |
| **`launch_pending_cmd(cmd=None, log=…)`** | 退出收尾**由此拉起**替换脚本：`CREATE_NO_WINDOW \| DETACHED_PROCESS`，返回是否已拉起（1.4.0）。别自己写 `os.system('start …')` |
| `http_error_hint(exc)` / `failed_marker_path(update_dir)` / `build_apply_script(…)` | 配额人话 / marker 路径 / 脚本生成（纯函数，供回归断言） |
| `UPDATE_READY` / `PENDING_CMD` | 兼容别名，**只读派生**（1.4.0）：模块里没有这两个全局，由 PEP 562 `__getattr__` 现算。外部请改用访问器 |
| **`log=` 的形态（稳定承诺）** | 收进来的 `log` 必须是 **print 形态**——本模块按 `log("downloading", stem)`、`log("update staged:", staged, "->", target, "(bat %s)" % s)` 调用（全文 7 处，最多 5 个位置参数），**只收一个 message 的 log 传进来，会在"每次下载"/"每次拉起替换脚本"这类真路径上直接 TypeError**。`log_kit.make_logger` 自 1.0.3 起即为该形态（C-29 机械检查工具侧包装是否照抄签名） |

> **状态一律经访问器读取（1.1.0 硬性口径；1.4.0 起结构上是唯一可能）**：状态住在
> `_PUBLISHED` **dict 里就地改**，唯一写入点；`UPDATE_READY` / `PENDING_CMD` 是
> `__getattr__` 的**只读派生值**，模块里**没有可被 `global` 重绑的标量**。
> 为什么较真到这个程度：旧版把状态写成模块级标量、函数里 `global` 重绑，而包门面
> `from .update_helper import *` 会拷走一份**静态副本** → 外部永远读到导入时的 `None`：
> 替换脚本永不拉起（更新装了等于没装）、"下载并更新"永久灰着，**且无任何报错**。
> 现在即便有人把 `import *` 加回来也复现不了——`import *` 不搬运 `__getattr__` 的派生名，
> 只会明确报"没有该属性"。
> 回归：`python my-diy-tool-template/sync_check.py --selftest`（E 组，含反向自证）
> 与 `conformance_check.py --selftest`（C-21 / C-23 正反样本）。

目标目录 `target_dir` 二选一：有稳定安装位的工具传 `INSTALL_DIR`（local-speak2text
形态）；没有的传运行中 exe 所在目录（dsh/opencodex 形态）。`update_dir` 必须在目标目录
**之外**（备份/快照/marker 都落在它旁边）。

## 替换脚本的语义要点（1.3.0/1.4.0，缺一即回归）

回归资产：`python my-diy-tool-template/sync_check.py --selftest`（E 组，含反向样本）。

1. **等待旧进程退出用镜像名，且不用管道**：脚本以 DETACHED_PROCESS 起，无 console；
   那种上下文里 `tasklist | find` **永不返回**（`find` 永远阻塞在 stdin），更新会静默不发生。
   故 `tasklist` **写文件**、`find` 读该文件。
2. **等待有上限**（`UPDATE_WAIT_LIMIT=120`，约 1 秒/次），超时走 `:giveup` 记日志。
3. **替换前先快照，且只在替换成功后轮转为备份**：旧备份是**回退源**，绝不能在新版落地前删。
4. **检查 robocopy 退出码（>=8 = 失败）**：失败时**绝不启动新 exe**，改为从快照回铺并启动
   旧版本；回铺也失败则**不启动任何 exe**，保留快照供人工恢复，并写失败 marker。
5. **失败 marker**（`update.failed`）：托盘已退出，只能下次启动由 `pop_failed_update_note()`
   读出来告诉用户；本次失败绝不留成"无声消失"。
6. 收尾分两条：成功/放弃删暂存（每次约 50MB）；失败路径**保留**暂存与快照供人工恢复。
   脚本本体在 %TEMP%，不在暂存目录内，故删暂存不会锁住正在执行的它；末尾自删。
7. **空暂存包在动手之前就拦下**（1.4.0）：暂存目录里没有 exe 时**绝不**开始拷贝——
   `robocopy /e /purge` 从空源返回 0–7，会把**安装目录清空**；随后的
   `start ""` 指向不存在的 exe 会弹出**没人能关的模态错误框**，bat 卡死。
   拦截后不碰安装目录、写 marker、把旧版本拉回来；**暂存整包保留**（1.4.1 起走
   `:cleanup_keep`，与 `:install_failed` 同一规则：失败要留现场给人工看）——
   回收交给启动期的 `sweep_stale_update_dirs()`（只清一小时前的）。
8. **每处 `start` 之前都有存在性守卫**（1.4.0）：回铺后 exe 仍不在 ⇒ 不启动（进 `:install_dead`）。
   启动一个不存在的 exe 是这套脚本里唯一会"卡死到永远"的动作。

## 打包约定（release.yml 必须满足）

- 资产名：`<APP_ID>-<版本>-windows-x64.zip` + 同名 `.sha256`（首行 `哈希  文件名`）；
  **`.sha256` 是必需资产**——缺失时更新会按"无法校验完整性"中止；
- zip 内**一层** `<APP_ID>-<版本>/` 目录，exe 名 = `EXE_NAME`（不带版本号）；
- CI 坑：`--specpath build` 时相对 `--add-data` 按 spec 目录解析 → 用 `$pwd\dir` 绝对路径。

## 采纳步骤

<!-- 下面三个符号是 C-27「采纳 = 拷贝 + 接线」的机械判据锚点：
     README 里声明"必须调用"，检查器就在**工具代码**里找引用；只在 README 出现不算接线。
     反面教材：dsh/ocx 曾把三件拷到位（哈希全绿）而 sweep/pop 零引用——失败通知永不触发。 -->
<!-- MUST-WIRE: sweep_stale_update_dirs -->
<!-- MUST-WIRE: pop_failed_update_note -->
<!-- MUST-WIRE: launch_pending_cmd -->

1. `appconfig.py` 填 `APP_ID / REPO_OWNER / REPO_NAME / EXE_NAME`；
2. 拷 `update_helper.py`（**零修改**）；
3. 托盘加"检查更新 / 下载并更新"两项（D14 更新区），quit 收尾处拉起 `pending_cmd()`；
4. 启动后台 `check_update(VERSION)` 一次；**启动时先** `sweep_stale_update_dirs()`，
   再 `pop_failed_update_note(update_dir)`，非空就通知用户；
5. 菜单项自行限制打包态（模板不做 frozen 判断）：dsh/ocx 在 `enabled=` 里判 `sys.frozen`。

## 边界与坑

- sha256 不匹配**或发布页缺 `.sha256`** 都中止，绝不落地（fail-closed 口径）；
- 更新器要等本进程消失才能换文件——退出路径必须保证真正退出；
- `VERSION` 不由本模块定义：各工具把它从**自己的单一事实源**读出来（STANDARDS D1），
  调 `check_update(VERSION)` 时传入；`appconfig.py` 不重复登记版本号。
