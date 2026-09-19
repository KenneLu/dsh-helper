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
| `migrate_autostart(log=…)` | 启动自愈：登记的 exe 已不存在 → 重写当前命令行 |

## 采纳步骤

1. 拷 `autostart.py`（依赖 `appconfig.APP_NAME` 与 T2 的 `INSTALL_EXE`）；
2. `main()` 早期调 `migrate_autostart()`；
3. 托盘菜单加"开机自启"开关项（D14 偏好区），checked 绑 `is_autostart_enabled()`。

## 边界与坑

- 键名用 `APP_NAME`（展示名）；**换名 = 断链**，历史工具改过名要写迁移。
- dsh-helper 的更严口径值得抄：勾选状态 = "登记命令行与当前命令行一致"（`_fold_command` 比对），不一致如实显示未勾选，**程序永不自动改写注册表**。
- exe 名永不带版本号（B3），否则每次升级自启必死链。
