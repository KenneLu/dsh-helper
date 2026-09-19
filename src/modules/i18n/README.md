# T5 · i18n —— 轻量中英双表

> 规范出处：STANDARDS.md §E4（T1 触发）、§A2 T1 行。
> 蓝本：local-speak2text/i18n.py（302 行完整版）。重形态（中文即键 + AST 覆盖审计）见 reme-helper/src/i18n.py，文案量百条级才需要。

## 定位

一张 zh 表 + 一张 en 表，**zh 键为基准**，en 缺失回退中文，永不 KeyError。
键名约定：`menu_* / notify_* / tray_* / status_* / update_*`。

## 对外接口（稳定承诺）

| 函数 | 说明 |
|---|---|
| `init(language="auto")` | `zh / en / auto`（auto 跟随 Windows UI 语言） |
| **`current_lang()`** | **当前语言（推荐读法，2.1.1）**——切语言后立刻反映真值 |
| `t(key, *args)` | 取词 + `%` 格式化 |
| `load_language_from_config(cfg) / save_language_to_config(cfg, lang)` | 语言持久化进 config.json 的 `language` 字段 |
| `detect_system_lang()` | Win32 UI 语言探测 |

> **状态一律经访问器读取（2.1.1 硬性口径）**：`LANG` 是**内部实现**，外部请用
> `current_lang()`。包门面（`modules/i18n/__init__.py`）**不做 `import *`**——那会把
> `LANG` 拷成静态副本，`init('en')` 后从包读仍是旧值，而 `t()` 已是英文。
> 症状：菜单签名算出来不变 → 切了语言**菜单不重建**（通知英文、菜单中文）。
> 实现为"只绑函数 + PEP 562 `__getattr__` 委派子模块"，故 `LANG` 每次读取都取真值。
> 回归：`python my-diy-tool-template/sync_check.py --selftest` 的 **D 组**（D1–D6）——
> **必须从包命名空间读**（`i18n.LANG`）才测得出；只读子模块会全绿。

## 采纳步骤

1. 拷 `i18n.py`，把 `_ZH/_EN` 换成本工具词表（**从第一天建**，事后补成本翻倍）；
2. `main()` 早期 `init(load_language_from_config(CONFIG_PATH))`；
3. 菜单加 `English / 中文` 切换项（**直接把两个语言名列出来**，zh/en 两表同值、**不随当前语言变**；不要写成 "Language 语言" 这类功能名），切换后**显式重建菜单**（D14）；
4. 构建门禁加 i18n 断言（关键键 zh/en 双查，参考 local-speak2text tests.yml）。

## 边界与坑

- 触发判断（A2 T1）：开源发布或交付他人 → 触发。README 双语（基线 8）是**另一条独立要求**，别混为一谈（2026-09-18 勘误）；
- 数据不动：路径、模型名、用户输入不参与翻译；
- dsh/opencodex 现状：UI 中文硬编码、T1 已登记为待办（见 REVIEW.md 复审发现），README 已双语。
