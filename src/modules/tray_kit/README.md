# T7 · tray_kit —— 托盘机制件（单实例 / 退出 / 掩码 / 签名重画）

> 规范出处：STANDARDS.md §E2（D14 八段菜单 + 命名五规则）、§G4.1；执行文档 F13/D11/D13。
> 蓝本：reme-helper（三循环、签名重画、退出纪律、托盘自愈）+ local-speak2text（单实例、`--quit` 请求文件）。

## 定位

纯函数库：不依赖任何具体工具，导入即用。**不含**设置窗口/主题/harness（二期件，D3 不抽）。
本模块是"标准即代码"：E2 的菜单八段与命名五规则、D11 的 token 掩码、F13 的
轮询节拍，落地全在这里。

## 对外接口（稳定承诺）

| 名称 | 说明 |
|---|---|
| `acquire_single_instance(app_id, mutex_name=None, log=…)` | 命名互斥体钉死单实例；守卫自身失败时**放行**（宁可多开不能打不开）；**名字非法同样放行**并记日志（D3.2），由构建期探针打红 |
| **`mutex_name_ok(name)`** | **名字形状判据**（2.2.0，**全家族只此一份**）：非空字符串 + `Local` 命名空间前缀 + 前缀之后不得再出现反斜杠。守卫 / 探针 / `single_instance_free` **共用同一段**，不写两份规则 |
| **`mutex_name_is_valid(app_id, mutex_name=None)`** | **`--smoke` 的守卫覆盖探针**（2.1.0，D3.1）：先过 `mutex_name_ok()`，再问"内核收不收"，**不占锁、不弹窗**。`ERROR_ALREADY_EXISTS` **也算合法**（名字被占用恰恰证明内核接受它），故与"此刻有没有实例在跑"无关——那是 `single_instance_free()` 的问题，拿它做冒烟断言会**假红** |
| `single_instance_free(mutex_name)` | 探测互斥体**此刻是否空着**，不持有（诊断用）。⚠️ 用户常驻实例在跑时返回 False——**不要**拿它做冒烟断言（见上面的探针） |
| `warn_duplicate_instance(app_name, hint=…, message=None, title=None)` | 重复启动弹窗（无 console 托盘程序 print 无人可见）；2.0.2 起文案可整体传入（i18n），不传用中文默认 |
| `confirm_quit_dialog(app_name, checkbox_text=None, checked_init=False, parent=None, on_change=None, *, title=None, body_text=None, confirm_text="退出", cancel_text="取消")` | 退出确认框（G4.1-04/G4.2-05）；返回 `{"go","stop_service"}`，取消返回 None。2.0.2：用户可见文案全部可传参（E4-02），`checkbox_text` 空则不渲染勾选框 |
| `make_quit_request_path(user_data_dir)` | `--quit` 请求文件路径；**纪律：必须配 T2 数据根重定向**（F11） |
| `quit_watch_loop(stop_event, path, on_quit, beat=1.0)` | 1s 拍监视请求文件，发现即删并回调 `on_quit`（走与托盘退出同一条清理路径） |
| `mask_token(url, keep="••••••")` | D11：展示面 token 全掩码；完整地址唯一入口 = 复制项 |
| `MenuSignature(rebuild, menu_is_open, log=…)` | 签名重画：`.update(sig)` 签名变了才重建菜单、菜单开着推迟；`.flush_deferred()` 给 1.5s 补画拍调用 |

## 三循环骨架（reme-helper 蓝本，组装规范）

| 循环 | 节拍 | 职责 |
|---|---|---|
| monitor_loop | 探测间隔，`WAKE_EVENT.wait()` | **首拍立即执行**（图标先立）；图标只在 visible 后改 |
| menu_refresh_loop | 1.5s | 只补"菜单开着时被推迟的重画" |
| quit_watch_loop | 1.0s | `--quit` 请求文件 |

退出纪律：`claim_shutdown` 防重入 + 超时兜底强退 Timer + 退出前排空在途动作；
二次确认只挂人工路径，`--quit` 绝不被弹窗卡住。
退出确认的**清理勾选**模式（G4.2 条款 5）：确认框内嵌"顺便关闭服务/隧道"两个
**持久化勾选、默认不勾**，开关透传给清理函数；参考实现 reme-helper 1.2.4 的
`confirm_quit_dialog` + `begin_shutdown(stop_tunnels, stop_reme)`。
确认框实现失败的降级链（规范 §G4.2）：富对话框 → 原生确认框 → 放行（唯一豁免：
无 UI 纯托盘）；**任何一级都不得跳过确认直接退**。
**i18n 纪律（2.0.2 / E4-02）**：本模块是纯机制件，用户可见文案一律由调用方经
`title/body_text/confirm_text/cancel_text/message` 传入（启用 i18n 的工具传 `t()` 词条）；
不传则用中文默认值——老调用点零改动。

## 采纳步骤

1. 拷 `tray_kit.py`（零依赖，纯标准库）；
2. `main()` 最早处 `acquire_single_instance()` → False 则 `warn_duplicate_instance()` 退出；
3. 菜单重建走 `MenuSignature`；管理 Web 面板的工具用 `mask_token` + 「复制面板地址」紧贴地址行；
4. 需要 `--quit` 的工具：`quit_watch_loop` + 独立数据区（T2）。

## 边界与坑

- 托盘图标注册被外壳拒绝时：重试 → 换 GUID 身份 → 响应 `TaskbarCreated`（reme-helper `recover_tray_registration`，必要时抄）；
- 菜单文案延迟求值（`lambda item:`），语言/状态变化才重建；
- 不为抽象而抽象：各工具的菜单业务项差异大，**菜单构建函数本身不模板化**，本模块只出机制件。
