# dsh 升级功能设计

> **任务**：dsh-helper 增加「当前 dsh 版本」显示 + dsh 升级流程引导。
> **配套文档**：[dsh-upgrade-guide.md](dsh-upgrade-guide.md)（升级流程，给最终用户看）
> **代码位置**：`src/main.py`（菜单在 `build_menu()`，约 1617 行）
> **状态**：设计稿，供开发实现

---

## 1. 需求与边界

### 1.1 要做什么

1. 托盘菜单显示**当前 dsh 版本**（参照同家族 reme-helper 的双版本行）。
2. 提供 dsh 升级流程引导：
   - 插件兼容性检查（记录哪些插件适配、哪些不适配）；
   - 升级步骤按 **Wave / Milestone** 组织；
   - 升级过程**记录到文档**，方便出问题排查；
   - 需要用户操作（如关闭 dsh）时明确提示，或给出可复制命令。

### 1.2 不做什么（明确边界）

- ❌ **不做一键升级 dsh**。升级有严格顺序（先插件、后宿主），自动执行风险高；用户明确要求不要求。
- ❌ **不自动停机**。停机是用户操作（点托盘「停止 dsh Web」），工具只提示。
- ❌ **不做动态兼容性检查**（不启动 dsh 去试）。检查是静态的，避免与「停机窗口只启动一次」冲突。
- ❌ 不替代 dsh 自身的 `dsh update` / `dsh plugin` 命令，只做引导与记录。

---

## 2. 菜单项形态

### 2.1 版本行（信息区第 2 行）

**位置**：`build_menu()` 的信息区，助手版本行之后、状态行之前。

```python
def build_menu():
    return pystray.Menu(
        # ① 信息区（只读）
        pystray.MenuItem(lambda _item: f"{APP_NAME} v{VERSION}", None, enabled=False),
        pystray.MenuItem(lambda _item: dsh_version_line(), None, enabled=False),   # ← 新增
        pystray.MenuItem(lambda _item: status_line(), None, enabled=False),
        pystray.MenuItem(lambda _item: url_line(), None, enabled=False),
        ...
```

**显示格式**（对齐 reme-helper 的「ReMe 版本：x.y.z」）：

| 情况 | 显示 |
|---|---|
| 已安装 | `dsh 版本：0.2.2-rc.1` |
| 未安装 / 读不到 | `dsh 版本：未安装` |
| 读取失败 | `dsh 版本：读取失败` |

**i18n 键**：`menu_dsh_version` = `"dsh 版本：%s"` / `"dsh version: %s"`；`menu_dsh_version_missing` = `"未安装"` / `"not installed"`。

### 2.2 升级流程入口（业务区）

**位置**：业务区（⑤），`dsh.cmd 路径` 之后。

```python
# ⑤ 业务区
pystray.MenuItem(i18n.t("menu_dsh_cmd"), build_dsh_command_menu()),
pystray.MenuItem(i18n.t("menu_dsh_upgrade"), build_dsh_upgrade_menu()),   # ← 新增
```

**子菜单结构**：

```
dsh 升级助手
├── 检查插件兼容性        → 生成 HTML 报告并打开
├── 打开升级流程文档      → 打开 doc/dsh-upgrade-guide.md
├── 打开升级记录          → 打开记录目录（或最近一份记录）
└── 复制升级命令          → 复制 npm 升级命令到剪贴板
```

**i18n 键**：

| 键 | 中文 | English |
|---|---|---|
| `menu_dsh_upgrade` | dsh 升级助手 | dsh Upgrade Assistant |
| `menu_upgrade_check` | 检查插件兼容性 | Check plugin compatibility |
| `menu_upgrade_open_doc` | 打开升级流程文档 | Open upgrade guide |
| `menu_upgrade_open_records` | 打开升级记录 | Open upgrade records |
| `menu_upgrade_copy_cmd` | 复制升级命令 | Copy upgrade command |

### 2.3 菜单签名（MENU_SIG）

版本行和升级菜单项的状态会变化，**必须把新状态纳入 `_menu_signature()`**，否则菜单不刷新（参考现有 `status_line()`/`url_line()` 的做法）。

```python
def _menu_signature():
    return (
        ...
        dsh_version_line(),        # ← 新增
        upgrade_report_outdated(), # ← 新增（有未读报告时提示）
    )
```

---

## 3. 当前 dsh 版本获取

### 3.1 数据来源

dsh 通过 npm 全局安装，版本在 `<npm_prefix>\node_modules\@deepseek-ai\dsh\package.json` 的 `version` 字段。

**npm prefix 的推导**（复用现有 `resolve_dsh_command()`）：

```python
def dsh_install_version() -> str | None:
    """读全局安装的 dsh 版本；读不到返回 None。"""
    cmd = resolve_dsh_command()            # 已配置的 dsh.cmd（唯一真源）
    if not cmd:
        return None
    prefix = Path(cmd).parent              # dsh.cmd 所在目录 = npm prefix
    candidates = [
        prefix / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",
        prefix.parent / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",  # 回退
    ]
    for pkg in candidates:
        try:
            if pkg.is_file():
                data = json.loads(pkg.read_text(encoding="utf-8"))
                return str(data.get("version") or "") or None
        except (OSError, ValueError):
            continue
    return None
```

> **为什么主读 package.json**：要起 Node 进程拿 `dsh --version` 慢且可能因 PATH/环境问题失败；读文件快且确定。
> **为什么不用 `npm prefix`**：`resolve_dsh_command()` 已经把 dsh.cmd 路径作为唯一真源，从它推导 prefix 最一致。

**运行态校验（可选）**：报告里可附一列「运行中进程版本」，通过 `dsh --version`（或读进程命令行）获取，与安装版本对比；不一致时提示「运行中的 dsh 与全局安装版本不一致，升级前请确认」。初版可省略，只报安装版本。

### 3.2 缓存与刷新

- **缓存**：模块级 `_DSH_VERSION_CACHE`，避免每次菜单刷新都读盘。
- **失效**：`refresh_state()` 时顺带刷新；或每次 `update_menu()` 时刷新（文件极小，直接读也可）。
- **兜底**：读不到时显示「未安装」，并在日志记一行。

### 3.3 版本行函数

```python
def dsh_version_line() -> str:
    version = dsh_install_version()
    if not version:
        return i18n.t("menu_dsh_version") % i18n.t("menu_dsh_version_missing")
    return i18n.t("menu_dsh_version") % version
```

---

## 4. 插件兼容性检查

### 4.1 输入

| 输入 | 来源 | 说明 |
|---|---|---|
| 目标 dsh 版本 | 用户输入（弹窗）或 `npm view` 查询 | 必填 |
| profile 目录 | `~/.dsh/profiles/<profile>/` | 默认 `web`；`DSH_HOME` 可覆盖 |
| 插件清单 | profile 的 `package.json` 的 `dependencies` + `dsh.profile.bundles` | |

**profile 路径解析**：

```python
def dsh_profile_dir(profile: str = "web") -> Path:
    """dsh 用户数据根：$DSH_HOME 优先，否则 ~/.dsh。"""
    root = os.environ.get("DSH_HOME", "").strip() or str(Path.home() / ".dsh")
    return Path(root) / "profiles" / profile
```

> **注意**：dsh-helper 目前不感知 profile，这是新增能力。默认 `web`（dsh-helper 管理的就是 dsh web）。如用户有多个 profile，后续可加选择器；初版固定 `web` 并在报告里写明。

### 4.2 检查流程

```
读 profile/package.json
  └─ dependencies → 插件名列表
对每个插件：
  读 node_modules/<plugin>/package.json
    ├─ 判据 A：engines.dsh 或 peerDependencies 的 @deepseek-ai/dsh-* 范围
    ├─ 判据 B：已移除接口扫描（初版标记「未知」，见 4.4）
    ├─ 判据 C：dependencies 里 @deepseek-ai/dsh-* 硬编码
    └─ 判据 D：C 命中包的传递依赖完整性
汇总 → HTML 报告
```

### 4.3 判据实现

#### 判据 A：版本范围声明

```python
def check_a(manifest: dict, target: str) -> CheckResult:
    # 1. 优先 engines.dsh
    engines_dsh = (manifest.get("engines") or {}).get("dsh")
    if engines_dsh:
        return CheckResult(passed=version_in_range(target, engines_dsh),
                           detail=f"engines.dsh = {engines_dsh}")
    # 2. 回退 peerDependencies 里的 @deepseek-ai/dsh-*
    peers = {k: v for k, v in (manifest.get("peerDependencies") or {}).items()
             if k.startswith("@deepseek-ai/dsh-")}
    if peers:
        # 取所有范围，任一覆盖目标线即通过
        for name, rng in peers.items():
            if version_in_range(target, rng):
                return CheckResult(passed=True, detail=f"{name} = {rng}")
        return CheckResult(passed=False, detail=f"peer 钉旧线: {peers}")
    return CheckResult(passed=None, detail="无版本声明")
```

**`version_in_range(version, range_str)`**：npm semver 范围匹配。建议实现一个简化版：

- 把 `||` 拆成多个子范围，任一命中即通过；
- 每个子范围按空格拆成 AND 条件；
- 支持操作符：`>=`、`>`、`<`、`<=`、`^`、`~`、`=`、裸版本号；
- 版本比较：按 `major.minor.patch` + prerelease 比较（prerelease 按字典序，`-rc.1` < `-rc.2` < 无 prerelease）；
- `^0.2.2-rc.1` → `>=0.2.2-rc.1 <0.3.0`；`^0.0.x` 特殊处理（npm 规则）。

> **备选**：如果自写 semver 风险高，可退化为「提取范围主版本线，与目标版本主版本线比较」——粗略但够用，报告里注明「初判，需人工复核」。

#### 判据 B：已移除接口扫描

**初版**：标记「未知」，报告里提示「需人工对照目标版本 CHANGELOG」。

**完整版**（后续）：维护一份「已移除 API 清单」（JSON 配置文件，随 dsh-helper 版本更新），对插件 `lib/**/*.js` 做符号搜索。

```python
REMOVED_APIS = {
    "0.2.0": ["readRaw", "loadStored", "readFrom", "inspect", ...],
    "0.3.0": [...],
}
```

#### 判据 C：dependencies 硬编码宿主包

```python
def check_c(manifest: dict) -> CheckResult:
    deps = {k: v for k, v in (manifest.get("dependencies") or {}).items()
            if k.startswith("@deepseek-ai/dsh-")}
    if not deps:
        return CheckResult(passed=True, detail="无硬编码")
    return CheckResult(passed=False, detail=f"dependencies 硬编码宿主包: {deps}")
```

> 注意：`@deepseek-ai/schemastery`、`@deepseek-ai/cordis` 是工具库，**不算**硬编码宿主包，不触发 C。

#### 判据 D：传递依赖完整性

```python
def check_d(manifest: dict, target_deps: dict) -> CheckResult:
    # 对 C 命中的包（或所有 @deepseek-ai/* dependencies），读其 package.json
    # 检查它声明的 dependencies/peerDependencies 是否在 target_deps 中
    ...
```

`target_deps` 来源：目标版本 dsh 的 `package.json` 的 `dependencies`（从 npm registry 拉，或读本地 `<prefix>/node_modules/@deepseek-ai/dsh/package.json`——注意这是**当前**版本，不是目标版本；目标版本的依赖清单需要从 npm 拉，初版可省略并标记「未知」）。

### 4.4 检查结论与分批

| 结论 | 条件 | 分批 |
|---|---|---|
| 适配 | A/B/C/D 全过（B/D 未知时按 A/C 判断） | A 批（可提前升） |
| 不适配 | A 不通过 或 C 命中 | B 批（进停机窗口） |
| 未知 | 信息不足 | B 批（保守处理） |

---

## 5. 报告生成

### 5.1 格式：HTML（浏览器打开）

**为什么用 HTML**：
- 托盘工具无复杂 UI，HTML 用浏览器打开体验最好；
- 表格、颜色、分层清晰，易读易分享；
- 用户可保存/打印/发给维护者。

### 5.2 报告内容

```
┌─────────────────────────────────────────┐
│ dsh 升级兼容性报告                        │
│ 检查时间 / 当前版本 / 目标版本 / profile  │
├─────────────────────────────────────────┤
│ 汇总：适配 N 个 / 不适配 N 个 / 未知 N 个 │
├─────────────────────────────────────────┤
│ 插件明细表                               │
│ 插件 | 版本 | A | B | C | D | 结论 | 说明 │
├─────────────────────────────────────────┤
│ 建议执行顺序                             │
│ A 批（Wave 2 升）：...                   │
│ B 批（Wave 3 升）：...                   │
├─────────────────────────────────────────┤
│ 风险提示 + 下一步操作                    │
└─────────────────────────────────────────┘
```

### 5.3 保存与打开

```python
REPORT_DIR = USER_DATA_DIR / "upgrade" / "reports"      # %LOCALAPPDATA%\dsh-helper\upgrade\reports\
RECORD_DIR = USER_DATA_DIR / "upgrade" / "records"

def save_report(html: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{timestamp()}.html"
    path.write_text(html, encoding="utf-8")
    return path

def open_report(path: Path) -> None:
    os.startfile(str(path))   # 浏览器打开
```

**为什么存用户数据区**：不进安装目录（OTA 更新会覆盖），用户可长期留存、多次升级对比。

### 5.4 报告样式

内联 CSS，简洁清晰：
- 适配 = 绿色，不适配 = 红色，未知 = 黄色；
- 表格等宽字体显示版本号；
- 顶部显示当前/目标版本对比。

---

## 6. 升级记录

### 6.1 记录内容

按 [dsh-upgrade-guide.md](dsh-upgrade-guide.md) 的模板生成 Markdown：

```markdown
# dsh 升级记录
- 当前版本：0.2.1-rc.2
- 目标版本：0.2.2-rc.1
- 检查时间：2026-11-07 14:30
...
```

### 6.2 记录时机

| 触发 | 记录内容 |
|---|---|
| 兼容性检查完成 | 检查结论摘要（各插件结论） |
| 用户点「复制升级命令」 | 记录复制的命令 |
| 用户点「打开升级记录」 | 打开目录 / 最近一份 |

### 6.3 实现

```python
def append_record(section: str, content: str) -> Path:
    """追加到当前升级批次的记录文件。"""
    path = current_record_path()          # records/<yyyyMMdd-HHmmss>-<from>-to-<to>.md
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {section}\n{content}\n")
    return path
```

---

## 7. 用户操作提示

### 7.1 提示方式

| 场景 | 方式 |
|---|---|
| 需要停机 | 托盘气泡通知 + 报告里写明「请点托盘『停止 dsh Web』」 |
| 需要跑命令 | 复制到剪贴板 + 气泡通知「命令已复制」 |
| 需要确认 | 原生弹窗（Tk）或气泡 |

### 7.2 复制升级命令

```python
def copy_upgrade_command(_icon=None, _item=None):
    target = ask_target_version()          # 弹窗输入目标版本
    if not target:
        return
    cmd = f'npm i -g "@deepseek-ai/dsh@{target}"'
    copy_to_clipboard(cmd)                 # 复用现有 copy_to_clipboard()
    notify(i18n.t("notify_cmd_copied") + "：" + cmd)
```

### 7.3 生成可执行脚本（可选增强）

把升级命令写成 `.ps1` 文件，用户双击执行：

```powershell
# dsh-upgrade-<version>.ps1
npm i -g "@deepseek-ai/dsh@<version>"
```

初版可不做，用「复制命令」兜底。

---

## 8. 目标版本获取

### 8.1 方式

1. **弹窗输入**：用户手动输入目标版本（默认填当前版本）；
2. **自动查询**：`npm view @deepseek-ai/dsh versions --json` 列出可选版本（后台线程，避免卡菜单）。

### 8.2 建议交互

点「检查插件兼容性」→ 弹窗：
```
当前 dsh 版本：0.2.1-rc.2
目标版本：[________]  （下拉可选 npm 上的版本）
[检查] [取消]
```

---

## 9. 文件与模块清单

| 内容 | 位置 | 说明 |
|---|---|---|
| 升级流程文档 | `doc/dsh-upgrade-guide.md` | 已创建，给最终用户 |
| 功能设计 | `doc/dsh-upgrade-feature-design.md` | 本文件 |
| 版本获取 | `src/main.py` 新增 `dsh_install_version()` / `dsh_version_line()` | |
| 兼容性检查 | `src/main.py` 新增 `check_plugin_compatibility()` 等 | 或拆 `src/upgrade_kit/` |
| 报告生成 | `src/main.py` 新增 `build_report_html()` / `save_report()` | |
| 升级记录 | `src/main.py` 新增 `append_record()` | |
| 菜单 | `src/main.py` `build_menu()` / `build_dsh_upgrade_menu()` | |
| i18n | `locales/zh.json` / `locales/en.json` | 新增 7 个键 |
| 报告目录 | `%LOCALAPPDATA%\dsh-helper\upgrade\reports\` | 用户数据区 |
| 记录目录 | `%LOCALAPPDATA%\dsh-helper\upgrade\records\` | 用户数据区 |

> **模块拆分建议**：如果代码量大，可拆 `src/upgrade_kit/`（version / compat / report / record 四个子模块），`main.py` 只做菜单接线。参考现有模板模块的拆分方式。

---

## 10. 设计决策与理由

| 决策 | 理由 |
|---|---|
| 版本行放信息区第 2 行 | 对齐 reme-helper 的双版本行形态；信息区是只读状态区，版本是状态 |
| 升级入口放业务区 | 业务区已有「dsh.cmd 路径」，升级是同类管理操作；不新开分区，保持八段式 |
| 兼容性检查是静态的 | 动态检查要启动 dsh，与「停机窗口只启动一次」冲突；静态检查足够初筛 |
| 报告用 HTML | 托盘无复杂 UI；HTML 浏览器打开体验好、易分享、可保存 |
| 报告/记录存用户数据区 | 不进安装目录（OTA 更新会覆盖）；用户可长期留存 |
| 不做一键升级 | 升级顺序有严格要求，自动执行风险高；用户明确要求不要求 |
| 不自动停机 | 停机是用户操作，工具只提示；避免误杀导致数据丢失 |
| 判据 B 初版标记「未知」 | 需要维护 API 变更清单，初版先提示人工复核；后续可补 |
| 判据 D 初版可简化 | 目标版本依赖清单需从 npm 拉；初版读本地当前版本清单 + 标记未知 |
| profile 固定 `web` | dsh-helper 管理的是 dsh web；多 profile 选择器留后续增强 |

---

## 11. 测试要点

- 版本行：有 dsh / 无 dsh / dsh.cmd 未配置 三种情况显示正确；
- 菜单签名：版本变化后菜单能刷新；
- 兼容性检查：无 profile / 空 profile / 插件缺失 package.json 时不崩；
- 判据 A：`^0.2.2-rc.1`、`>=0.2.0-0 <0.3.0`、`||` 多范围等边界；
- 判据 C：`dependencies` 含 `@deepseek-ai/dsh-*` vs 仅 `@deepseek-ai/schemastery`；
- 报告：HTML 生成、路径、浏览器打开；
- 记录：追加不覆盖、目录自动创建；
- i18n：中英文键齐全（跑现有 `tests/test_i18n_keys.py`）；
- 现有测试全绿（`tests/` 下 pytest）。
