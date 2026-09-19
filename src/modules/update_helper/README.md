# T4 · update_helper —— 在线更新三段式（查 → 下 → 换）

> 规范出处：STANDARDS.md §G4/G4.1、§H 发布链规范 6（zip 约定）。
> 蓝本：reme-helper 的查-下-换设计 + local-speak2text/updater.py（稳定位形态）+ dsh/opencodex（运行目录形态）。
> **本模块是"接口化"的旗舰案例**：dsh-helper 与 opencodex-helper 的副本与模板正文完全一致，差异全部收敛进各自的 `appconfig.py`。

## 定位

三段式：**查**（GitHub Releases latest，内存 24h 节流）→ **下**（zip + `.sha256`
校验，不匹配即中止）→ **换**（写一次性 apply.cmd：等本进程退出 → `robocopy /MIR`
铺目标目录 → 重启新 exe → 自删）。为什么换目录必须独立脚本：Windows 上运行中的
exe 换不掉。

## 对外接口（稳定承诺）

| 函数/全局 | 说明 |
|---|---|
| `check_update(current_version, force=False)` | 返回 `dict(latest, current, newer, error)`；失败写 `error` 不抛。**成功时顺带更新 `UPDATE_READY`**（1.1.0 起） |
| `download_and_prepare(latest, target_dir, update_dir, log=…)` | 下载+校验+暂存+生成 apply.cmd；成功返回脚本路径并置 `PENDING_CMD` |
| **`update_ready()`** | 有新版时的版本号，否则 `None`（**推荐读法**，1.1.0）。"下载并更新"菜单可用性据此判断 |
| **`pending_cmd()`** | 已就绪的脚本路径，否则 `None`（**推荐读法**，1.1.0）。托盘退出后由此拉起 |
| `UPDATE_READY` / `PENDING_CMD` | 同上的**内部状态**；保留兼容，外部请改用访问器 |

> **状态一律经访问器读取（1.1.0 硬性口径，同 i18n 2.1.1）**：包门面
> （`modules/update_helper/__init__.py`）**不做 `import *`**——那会把 `UPDATE_READY` /
> `PENDING_CMD` 拷成静态副本，函数里 `global` 重绑的是子模块那份，外部读包**永远拿到
> 导入时的 None**：`apply.cmd` 永不拉起（更新装了等于没装），"下载并更新"永久灰着，
> **且无任何报错**。实现为"只绑函数 + PEP 562 `__getattr__` 委派子模块"。
> 回归：`python my-diy-tool-template/conformance_check.py --selftest`（C-21 正/反样本）。

目标目录 `target_dir` 二选一：有稳定安装位的工具传 `INSTALL_DIR`（local-speak2text
形态）；没有的传运行中 exe 所在目录（dsh/opencodex 形态）。

## 打包约定（release.yml 必须满足）

- 资产名：`<APP_ID>-<版本>-windows-x64.zip` + 同名 `.sha256`（首行 `哈希  文件名`）；
- zip 内**一层** `<APP_ID>-<版本>/` 目录，exe 名 = `EXE_NAME`（不带版本号）；
- CI 坑：`--specpath build` 时相对 `--add-data` 按 spec 目录解析 → 用 `$pwd\dir` 绝对路径。

## 采纳步骤

1. `appconfig.py` 填 `APP_ID / REPO_OWNER / REPO_NAME / EXE_NAME`；
2. 拷 `update_helper.py`（**零修改**）；
3. 托盘加"检查更新 / 下载并更新"两项（D14 更新区），quit 收尾处拉起 `PENDING_CMD`；
4. 启动 8 秒后后台 `check_update(VERSION)` 一次。

## 边界与坑

- sha256 不匹配即中止，绝不落地；
- 源码运行态调用 `download_and_prepare` 直接抛错（无 exe 可替换）；
- 更新器要等本进程消失才能换文件——apply.cmd 自带 2 秒等待；退出路径必须保证真正退出。
