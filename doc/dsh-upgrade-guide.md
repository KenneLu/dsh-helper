# dsh 升级流程指南

> **适用**：本机通过 npm 全局安装的 dsh（DeepSeek Harness CLI）需要跨版本升级，且 profile 里装了若干插件。
> **目标**：把 dsh 从当前版本升到目标版本，同时保证插件可用、会话不丢、出问题能回退。
> **使用方式**：按 Wave 顺序执行；每完成一个 Milestone 就在「升级记录」里写一行。**不要跳步、不要改顺序。**

---

## 0. 先记住三条前提（否则整个计划会建立在错误假设上）

1. **先插件，后宿主。** dsh 升级有严格顺序：插件必须先确认适配目标版本，再升 dsh 本体。顺序反了，dsh 可能启动失败。
2. **停机窗口内只启动一次。** 「升宿主 + 升必须在目标宿主上装的插件 + 启动」放在同一个停机窗口里完成。中途每多启动一次 dsh，就多一次踩雷机会。
3. **备份是移动靶。** 升级期间会话仍在写入，备份必须带时间戳、且逐文件哈希留档；拷贝完立刻做哈希快照，那才是回退验收的基线。

---

## 1. 升级前准备

### 1.1 确认当前版本与目标版本

```powershell
# 当前安装版本
dsh --version

# 或者直接读全局包清单（更可靠，不依赖 PATH）
node -p "require('$((npm config get prefix) -replace '\\','/')/node_modules/@deepseek-ai/dsh/package.json').version"

# 查所有可装版本
npm view @deepseek-ai/dsh versions --json
npm view @deepseek-ai/dsh dist-tags --json
```

- **当前版本**：`dsh --version` 的输出。
- **目标版本**：从 `npm view` 的结果里选。
- ⚠️ **通道陷阱**：`dsh update` / `npm i -g @deepseek-ai/dsh` 默认跟 `latest` 通道，而包含所需适配的可能是 `next` 通道。**一律显式指定版本号**（`@<version>`），不要依赖默认通道。
- ⚠️ 升级宿主不等于能力升级：模型没换，智力不变。

### 1.2 Node 版本检查

```powershell
node -v
```

dsh 对 Node 版本有硬性要求，以**目标版本**的 `engines.node` 为准：

```powershell
node -p "require('$((npm config get prefix) -replace '\\','/')/node_modules/@deepseek-ai/dsh/package.json').engines.node"
```

当前（0.2.x）要求 `>=24.9.0`。**Node 版本不满足时先升级 Node**，否则装上了也跑不起来。

### 1.3 确认 npm 全局目录

```powershell
npm config get prefix
```

记下这个路径，后面备份和验证都要用。dsh 本体装在 `<prefix>\node_modules\@deepseek-ai\dsh\`。

### 1.4 备份

**备份目录约定**：`%USERPROFILE%\dsh-backups\<yyyyMMdd-HHmmss>-<当前版本>-to-<目标版本>\`

**备份内容**：

| 内容 | 路径 | 为什么必须备份 |
|---|---|---|
| 会话 | `%USERPROFILE%\.dsh\sessions\` | 唯一回退依据；升级可能触发会话格式迁移 |
| profile 清单 | `%USERPROFILE%\.dsh\profiles\<profile>\package.json` | 插件清单，回退要照它还原 |
| profile 补丁 | `...\cordis.patch.yml` | 本地配置补丁 |
| profile 工作区 | `...\pnpm-workspace.yaml` | 网络/构建配置 |
| profile 锁文件 | `...\pnpm-lock.yaml` | 依赖锁定 |
| 本地插件 | profile 里 `link:` 指向的目录 | 可能是唯一副本（没有 src/.git） |
| 全局 dsh | `<prefix>\node_modules\@deepseek-ai\dsh\` | 回退阶梯 3 要用 |

> `<profile>` 默认是 `web`（dsh web 使用的 profile）。如果用了 `DSH_HOME` 环境变量，路径相应改为 `%DSH_HOME%\profiles\<profile>\`。

**备份命令**（PowerShell，`<profile>` 按实际替换）：

```powershell
$ts   = Get-Date -Format "yyyyMMdd-HHmmss"
$ver  = (dsh --version).Trim()
$root = "$env:USERPROFILE\.dsh"
$dst  = "$env:USERPROFILE\dsh-backups\$ts-$ver-to-<目标版本>"

robocopy "$root\sessions"          "$dst\sessions"  /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP
robocopy "$root\profiles"          "$dst\profiles"  /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP
robocopy "$((npm config get prefix))\node_modules\@deepseek-ai\dsh" "$dst\dsh-host" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP
```

**哈希快照（拷贝后立刻做，这是回退验收的唯一基线）**：

```powershell
Get-ChildItem "$dst\sessions" -Recurse -File |
  Get-FileHash -Algorithm SHA256 |
  Export-Csv "$dst\sessions-hashes.csv" -NoTypeInformation

# 数量与字节数也要留档
$files = Get-ChildItem "$dst\sessions" -Recurse -File
"count=$($files.Count) bytes=$(($files | Measure-Object Length -Sum).Sum)" |
  Out-File "$dst\sessions-stats.txt" -Encoding utf8
```

> ⚠️ 备份是移动靶：拷贝期间会话仍在写入。**数量对 + 字节数对 + 逐文件哈希对，三样都要**，且必须在拷贝后立刻做。

**记录要求**：备份目录路径、文件数、总字节数、哈希快照路径、开始/结束时间。

---

## 2. 插件兼容性检查（四条判据）

**目标**：判断 profile 里每个插件是否适配目标 dsh 版本。四条判据缺一条就会出事。

**检查对象**：`%USERPROFILE%\.dsh\profiles\<profile>\package.json` 里 `dependencies` 列出的插件（以及 `dsh.profile.bundles` 里实际加载的）。

**插件 package.json 位置**：`%USERPROFILE%\.dsh\profiles\<profile>\node_modules\<plugin>\package.json`

---

### 判据 A：插件声明的版本范围

**看什么**：插件 package.json 的 `engines.dsh`（优先）或 `peerDependencies` 里的 `@deepseek-ai/*` 范围。

**怎么判断**：

1. **`engines.dsh` 存在** → 这是插件作者**显式声明**的 dsh 版本范围，目标版本落在范围内即通过。
   - 例：`engines.dsh = ">=0.1.2-rc.1 <0.2.0 || >=0.2.0-0 <0.3.0"`，目标 `0.2.2-rc.1` → 命中 `>=0.2.0-0 <0.3.0` → **通过**。
   - 判断方法：用 semver 库做范围匹配（把 `||` 拆成多个范围，任一命中即通过）。
2. **`engines.dsh` 不存在** → 看 `peerDependencies` 里 `@deepseek-ai/dsh-*` 的范围，是否覆盖目标版本线。
   - 例：`"@deepseek-ai/dsh-agent": "^0.2.2-rc.1"` → 期望 0.2.2 线 → **通过**。
   - 例：`"@deepseek-ai/dsh-agent": "^0.1.5-rc.1"` → 期望 0.1.5 线 → **不通过**。
3. **都没有** → **未知**（信息不足，按 B/C/D 判断）。

⚠️ **范围相交 ≠ 兼容。** `^0.1.2-rc.1` 解析为 `>=0.1.2-rc.1 <0.2.0`，天然包含 `0.1.5-rc.2`——所以「范围覆盖」这件事**证明不了任何东西**。必须看作者是否**主动换线**（判据 A）+ 代码是否踩已移除接口（判据 B）+ 依赖是否硬编码（判据 C/D）。

---

### 判据 B：已移除接口扫描

**看什么**：插件已发布的 JS 里，是否使用了目标版本**已移除**的 API。

**怎么判断**：

1. 查目标版本的 CHANGELOG / 发布说明，列出「已移除 / 已更名 / 已废弃」的 API 符号清单。
2. 在插件 `node_modules/<plugin>/lib/**/*.js` 里搜索这些符号。
3. **命中 → 不适配**。
4. 没有变更清单可查 → **未知**。

> 典型：宿主某版本把「直接读写会话的函数」换成了「句柄式 API」，插件如果还在调旧函数就会启动即崩。这类问题判据 A 查不出来，只能靠判据 B。

---

### 判据 C：dependencies 里的宿主包硬编码

**看什么**：插件 package.json 的 **`dependencies`**（**不是** peerDependencies）。

**怎么判断**：

1. 检查 `dependencies` 里有没有 `@deepseek-ai/dsh-*` 包。
2. 如果有，且版本是**钉死的具体版本**（或另一条版本线）→ 插件把宿主包当普通依赖装，会装到错误版本线。
3. **命中 → 高风险，不适配**。这是已实证的「启动即崩」杀手：`ERR_MODULE_NOT_FOUND: Cannot find package '@deepseek-ai/xxx'`。

> ⚠️ 注意：`dependencies` 里出现 `@deepseek-ai/schemastery`、`@deepseek-ai/cordis` 这类**工具库**是正常的（它们不随宿主版本走）。重点盯 `@deepseek-ai/dsh-*` 前缀的包。

---

### 判据 D：传递依赖完整性

**看什么**：被插件拉进来的宿主包，**它自己**的 dependencies / peerDependencies 在当前宿主里是否都存在。

**怎么判断**：

1. 读插件 `dependencies` 里 `@deepseek-ai/*` 包（或 C 命中的包）的 package.json。
2. 检查它声明的依赖是否在目标宿主中存在（对照目标版本 dsh 的 `dependencies` 清单）。
3. **缺包 → 运行期报错**。

---

### 检查结论汇总

| 插件 | A 版本范围 | B 已移除接口 | C dependencies 硬编码 | D 传递依赖 | 结论 | 说明 |
|---|---|---|---|---|---|---|
| （示例）dsh-mcp-panel | ✅ | ✅ | ✅ | ✅ | 适配 | |
| （示例）dsh-foo | ✅ | ⬜ 未知 | ⬜ 未知 | ⬜ 未知 | 未知 | 需人工确认 |
| （示例）dsh-bar | ❌ | — | — | — | 不适配 | peer 钉 0.1.x 线 |

**分批**（决定插件在哪个 Wave 升）：

- **A 批（可提前升）**：A/B/C/D 全过、且在新旧宿主上都能跑的插件 → 放在 **Wave 2**（不停机，dsh 还在跑时升）。
- **B 批（必须进停机窗口）**：只能在目标宿主上装的（C/D 命中、或 A 不通过、或依赖新宿主包）→ 放在 **Wave 3**（停机后升）。

**判定「能不能在旧宿主上先升」**：扫新版插件是否用了**旧宿主没有**的接口/包（反向判据）。带 `typeof x === "function"` 特性探测的插件通常两边都能跑，但要逐个看，不能假设。

---

## 3. 升级步骤

### Wave 1：备份（不停机）

**执行者**：用户（工具可生成备份命令并提示）。

| Milestone | 动作 | 完成标志 |
|---|---|---|
| 1.1 | 备份 sessions 目录 | 备份目录存在，文件数与源一致 |
| 1.2 | 备份 profile 配置（package.json / cordis.patch.yml / pnpm-workspace.yaml / pnpm-lock.yaml） | 四个文件都在 |
| 1.3 | 备份本地插件（`link:` 指向的目录） | 目录复制完成 |
| 1.4 | 备份全局 dsh 本体 | `dsh-host` 目录存在 |
| 1.5 | 哈希快照 | `sessions-hashes.csv` + `sessions-stats.txt` 生成 |

**记录要求**：备份目录路径、文件数、字节数、哈希快照路径、开始/结束时间。

**用户操作提示**：工具给出备份命令，用户复制执行；或工具直接执行只读备份（不碰 dsh）。

---

### Wave 2：插件升级（不停机，dsh 仍在跑）

**前提**：兼容性检查完成，A 批插件确认可升。

| Milestone | 动作 | 完成标志 |
|---|---|---|
| 2.1 | 升级 A 批插件 | `pnpm up <plugin>` 成功 |
| 2.2 | 重启 dsh 验证 | 面板可打开、A 批插件功能正常 |

**用户操作**（工具给出命令，用户执行）：

```powershell
cd "$env:USERPROFILE\.dsh\profiles\<profile>"
pnpm up <plugin1> <plugin2> ...
```

**记录要求**：升级前/后版本对照、完整命令、输出摘要、重启验证结果（含插件功能抽查）。

> ⚠️ Wave 2 结束后**不要**升 dsh 本体。先确认 A 批插件在新版本下正常。

---

### Wave 3：停机升 dsh（停机窗口）

**执行者**：用户。**这是唯一允许启动 dsh 的窗口。**

**执行结构（唯一正确）**：

```
① 优雅停 dsh           ← 用户点托盘「停止 dsh Web」；停机期间绝不启动
② 升 dsh 本体          ← npm i -g @deepseek-ai/dsh@<目标版本>
③ 升 B 批插件          ← 只能在目标宿主上装的插件
④ 启动 dsh（唯一一次） ← 用户点托盘「启动 dsh Web」
⑤ 按验收清单逐项核
```

> ②③ 先后无所谓（停机期间没人加载插件）；**建议 ② 在前**——profile 重算依赖时环境才自洽。

| Milestone | 动作 | 完成标志 |
|---|---|---|
| 3.1 | 优雅停机 | 托盘状态变「已停止」，进程消失 |
| 3.2 | 升 dsh 本体 | `dsh --version` == 目标版本 |
| 3.3 | 升 B 批插件 | `pnpm up <B批插件>` 成功 |
| 3.4 | 启动 dsh | 托盘状态变「运行中」，面板可打开 |
| 3.5 | 记录启动日志摘要 | 保存日志片段 |

**用户操作提示**：

1. **停机**：点托盘「停止 dsh Web」，等状态变「已停止」。
   - ⚠️ 必须**优雅停机**（托盘发的 Ctrl+C），不要直接任务管理器杀进程——优雅退出会全量冲刷会话与记忆，强杀会丢。
   - ⚠️ 停机期间**绝不启动 dsh**。
2. **升宿主**（复制执行）：

   ```powershell
   npm i -g "@deepseek-ai/dsh@<目标版本>"
   ```

3. **升 B 批插件**：

   ```powershell
   cd "$env:USERPROFILE\.dsh\profiles\<profile>"
   pnpm up <B批插件1> <B批插件2> ...
   ```

4. **启动**：点托盘「启动 dsh Web」。

**记录要求**：停机时间、升级命令**原文**、完整输出（尤其报错）、启动时间、启动结果（成功/失败 + 错误摘要）、临时禁用的插件清单。

> **处置优先级（写死）**：**宿主能开 > 插件全绿**。遇到让启动退出的插件，按回退阶梯处理或**临时禁用 + 把例外记进验收表**，不要为了插件全绿把整个窗口卡死。

---

### Wave 4：验收

| Milestone | 判据 | 通过标准 |
|---|---|---|
| 4.1 版本 | `dsh --version` == 目标版本 | 一致 |
| 4.2 服务 | 面板可打开 | HTTP 200（或 3xx 跳转换 cookie） |
| 4.3 会话 | 数量不变 + 与备份**逐文件哈希一致** | 完全一致 |
| 4.4 插件 | 逐个看启动日志 | 无 skip / 无 pending / 无 ValidationError；例外已记录 |
| 4.5 关键通路 | 记忆读写 / MCP 工具 / 侧边栏 / 模型选择 / 文件工具 | 各实测一次 |
| 4.6 回退材料 | 备份 + 哈希快照 + 回退命令 | 都还在 |

**记录要求**：每项验收结果（通过/失败/例外）、例外说明、验收人、时间。

> ⚠️ **「写出来了」不等于「能用」**：每项验收都要跑到**用户真正要做的那一步**为止（例如导入 → 打开 → 续聊），中途任何一环通过都不算通过。

---

## 4. 记录要求（贯穿全程）

**记录位置**：`%LOCALAPPDATA%\dsh-helper\upgrade\records\<yyyyMMdd-HHmmss>-<当前版本>-to-<目标版本>.md`
（工具自动生成：点托盘「dsh 升级助手 → 检查插件兼容性 / 复制升级命令」即开一个批次记录并追加；
「打开升级记录」打开的就是这个目录。与 §1.4 的**备份目录**（`%USERPROFILE%\dsh-backups\…`）
是两回事：备份是回退材料，记录是过程留痕。）

**每个 Wave 至少记录**：

- 开始 / 结束时间
- 执行的操作（**命令原文**，不要只写描述）
- 结果（成功 / 失败 + 关键输出）
- 异常与处置（含临时禁用的插件）
- 操作人

**模板**（照抄）：

```markdown
# dsh 升级记录

- 当前版本：
- 目标版本：
- 开始时间：
- 结束时间：
- 操作人：

## Wave 1 备份
- 备份目录：
- 文件数 / 字节数：
- 哈希快照：
- 结果：

## Wave 2 插件升级
- A 批插件版本变化：
- 命令：
- 结果：

## Wave 3 停机升 dsh
- 停机时间：
- 升级命令：
- B 批插件：
- 启动结果：
- 异常与处置：

## Wave 4 验收
- 版本：
- 服务：
- 会话：
- 插件：
- 关键通路：
- 例外：

## 回退（如发生）
- 阶梯：
- 动作：
- 结果：
```

---

## 5. 用户操作提示（工具要做的）

| 步骤 | 谁做 | 工具怎么提示 |
|---|---|---|
| 备份 | 用户 | 给出备份命令（可复制），或工具执行只读备份 |
| 停机 | 用户 | 提示「请点托盘『停止 dsh Web』，等状态变已停止」 |
| 升宿主 | 用户 | 给出 `npm i -g "@deepseek-ai/dsh@<版本>"` 命令（可复制） |
| 升插件 | 用户 | 给出 `pnpm up ...` 命令（可复制） |
| 启动 | 用户 | 提示「请点托盘『启动 dsh Web』」 |
| 验收 | 用户 | 给出验收清单，逐项确认 |

**工具负责**：检查兼容性、生成报告、给出命令、记录过程、提示下一步。
**工具不做**：自动执行升级、自动停机（停机是用户操作）。

---

## 6. 回退方案（三阶梯，预先写死）

**失败时机械执行，不要现场判断。**

| 阶梯 | 动作 | 适用场景 |
|---|---|---|
| 1 | 回退**最可疑的那个插件**到旧版（用精确版本号）→ 重启试 | 单个插件导致启动失败 |
| 2 | 再回退**其余新装插件** → 重启试 | 多个插件疑似冲突 |
| 3 | `npm i -g @deepseek-ai/dsh@<旧版本>` → 重启试 | 宿主本身有问题 |

⚠️ **阶梯 3 的前提**：已被迁移到新格式的会话，旧版可能读不回来。走这步前先记录哪些会话已迁移。

**回退后必须做落地核对**（否则分不清是「回退成功」还是「没回退干净」）：

- [ ] 宿主已回到旧版（`dsh --version` 确认）
- [ ] profile 里的 `@deepseek-ai/*` 残留包已清空
- [ ] lockfile 包集合净变化为 0
- [ ] 按判据 C 重扫全部插件

**回退记录**：阶梯、动作、结果、核对结论。

---

## 7. 验收清单（照抄到记录里）

| 项 | 判据 | 通过 |
|---|---|---|
| 宿主版本 | 全局 package.json 与运行中进程一致 | ☐ |
| 服务可用 | 端口监听 + 面板 HTTP 200 | ☐ |
| 会话完整 | 数量不变 + 与备份逐文件哈希一致 | ☐ |
| 插件加载 | 逐个看启动日志，例外已记录 | ☐ |
| 关键通路 | 记忆 / MCP / 侧边栏 / 模型选择 / 文件工具各实测一次 | ☐ |
| 回退材料 | 备份 + 哈希快照 + 回退命令都在 | ☐ |

---

## 8. 别做的

- ❌ 不要没备份就升宿主
- ❌ 不要中途反复启动 dsh（停机窗口内只启动一次）
- ❌ 不要删会话文件（那是唯一回退依据）
- ❌ 不要改 `cordis.yml`（bundle 生成物）——只改 `cordis.patch.yml`
- ❌ 不要 `npm uninstall` / 删 `node_modules`
- ❌ 不要指望「peer 范围相交」就兼容
- ❌ 不要用 `latest` 通道升级（显式指定版本号）
- ❌ 不要在 Node 版本不满足时强行安装

---

## 9. 常见问题

**Q：插件没有 `engines.dsh` 也没有 `@deepseek-ai/*` peer 怎么办？**
A：判据 A 判为「未知」，靠 B/C/D 兜底；仍无法判断时，把插件归为 B 批（进停机窗口升），升完重点验证。

**Q：兼容性检查说「适配」，升完还是崩了？**
A：检查是**静态**的，覆盖不了运行期问题。以启动日志为准，按回退阶梯处理。

**Q：备份的会话哈希和升级后不一致？**
A：正常——升级后续写会生成新世代文件，**旧世代文件应逐字节不变**。核对方式是：旧文件哈希不变 + 新文件是新增。

**Q：`pnpm up` 卡住/超时？**
A：检查 `pnpm-workspace.yaml` 的网络配置（`networkConcurrency` / `fetchTimeout` / `fetchRetries`）。改完重试。

**Q：升级后插件功能缺失但没崩溃？**
A：可能是插件被 boot 静默 skip。查启动日志的 `skipping profile bundle` 关键字。
