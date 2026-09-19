# T3 · autostart —— 开机自启三件套

> 规范出处：STANDARDS.md §G4.1（规范原文认定 local-speak2text 形态"比 reme-helper 更进一步"）。
> 蓝本：local-speak2text/main.py 自启段。

## 定位

`HKCU\...\Run` 用户级自启（免管理员）。三件套：**指向稳定位**（更新不改路径）、
**设置/取消**、**启动自愈**（注册表指向的 exe 消失时重写）。

## 对外接口（稳定承诺）

| 函数 | 说明 |
|---|---|
| `get_autostart_cmd()` | 打包实例优先返回 `INSTALL_EXE`（T2 稳定位）；稳定位尚无 exe 时退回当前路径；源码态用 pythonw |
| `is_autostart_enabled()` | Run 项存在即 True |
| `set_autostart(enabled)` | 写/删 `Run\<APP_NAME>` |
| `migrate_autostart(log=…)` | 启动自愈：登记的 exe 已不存在 **或指向的不是当前命令行** → 重写当前命令行（1.1.3）。条目本就不存在时**不新建、只记一行日志**。⚠ **存在性谓词不能代理"当前性"**：本家族 `release/` 保留历史版本目录 ⇒ "旧版 exe 还在"恒真 ⇒ 旧实现（只看存在）对"指向废弃版本"的自启项**永不自愈**（实例：dsh 的 Run 键曾指 `release\dsh-helper-1.8.2\`） |

## 采纳步骤

1. 拷 `autostart.py`（依赖 `appconfig.APP_NAME` 与 T2 的 `INSTALL_EXE`）；
2. `main()` 早期调 `migrate_autostart()`；
3. 托盘菜单加"开机自启"开关项（D14 偏好区），checked 绑 `is_autostart_enabled()`。

## 边界与坑

- 键名用 `APP_NAME`（展示名）；**换名 = 断链**，历史工具改过名要写迁移。
- dsh-helper 的更严口径值得抄：勾选状态 = "登记命令行与当前命令行一致"（`_fold_command` 比对），不一致如实显示未勾选，**程序永不自动改写注册表**（指勾选状态本身；`migrate_autostart` 修死链是另一件事）。
- exe 名永不带版本号（B3），否则每次升级自启必死链。
- **注册值是一条完整命令行**（打包态 `"…\app.exe"`，源码态 `"…\pythonw.exe" "…\main.py"`）。
  判"目标是否存在"必须按命令行取**全部带引号的路径 token**（`_cmd_targets` / `_cmd_alive`）：
  ① `value.strip('"')` 对源码态会得到 `…pythonw.exe" "…main.py` 这种不可能存在的路径，
  于是只要注册命令行与当前命令行有一丁点不同就**白写一次注册表**；② 只看第一个 token 又会
  漏掉"解释器还在、入口脚本没了"的死链（开机什么都不发生，看着却像健康）。
- **自愈不创建条目**（1.1.2 定）：条目不存在（用户关掉了自启 / 被杀软清了）时只记一行日志。
  自动创建等于替用户把他关掉的自启重新打开。同理，`except: pass` 让"本来就没这条"与
  "读失败"在日志里同形，现场无法区分 —— 不新建 ≠ 不出声。
