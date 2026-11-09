# 临时目录门禁自证记录（2026-11-09）

> **用途**：验收材料。证明「临时目录泄漏门禁」本身有效——能报红、也能报绿，且不误报 update 暂存。
> **执行人**：复核员（独立复跑，非开发自报）。
> **被验对象**：dsh-helper `c797b4e`（门禁薄封装）+ 模板 `temp_kit`（子模块 `9e9b123`）。
> **跑法**：`python tests/temp_gate_selfcheck.py`（退出码 0 = 自证通过）。

## 一、门禁自证四拍

| # | 拍 | 期望 | 实测 rc | 判定 |
|---|---|---|---|---|
| 0 | update 暂存不误报（根级真实形态） | 0 | 0 | PASS |
| 1 | 基线绿（无泄漏） | 0 | 0 | PASS |
| 2 | 注入泄漏 → 红 | 1 | 1 | PASS |
| 3 | 回滚 → 绿 | 0 | 0 | PASS |

**总判定：`GATE SELFCHECK OK`（exit 0）**

### 原始输出

```text
[PASS] update staging not flagged (expect 0) -> True
[PASS] baseline green (expect 0)          -> True
[PASS] leak injected -> red (expect 1)    -> True
[PASS] rollback -> green (expect 0)       -> True
GATE SELFCHECK OK
```

## 二、复核员独立复验（同一份代码，另跑一遍）

| 场景 | 操作 | 期望 rc | 实测 rc | 判定 |
|---|---|---|---|---|
| 根级旧形态泄漏 | 建 `%TEMP%\dsh-helper-rootleak-abcd1234\` | 1 | 1 | PASS |
| update 暂存不误报 | 建 `%TEMP%\dsh-helper-update-abc12345\` + `dsh-helper-update.bat` | 0 | 0 | PASS |
| 工具根本身不算泄漏 | 只留 `%TEMP%\dsh-helper\`（空） | 0 | 0 | PASS |

## 三、构建与残留（同轮实测）

- `build.bat norun nopause` **全绿**：`[GATE] temp baseline saved: %TEMP%\dsh-helper* (0 entries)` → 测试 → `[GATE] temp dir leak: 0 new entries` → sync_check → OTA → frozen smoke → `[DONE] release: release\dsh-helper-1.8.14\\`。
- 4 工具 `%TEMP%` 残留：`dsh-helper` / `opencodex-helper` / `ocx-*` / `reme*` / `local-speak2text` / `l-s2t-*` **均为 0**（`%TEMP%\dsh-helper\` 是工具自己的空临时根，不算残留）。

## 四、判据说明

- **拍 0** 验证门禁与 `temp_kit.sweep` 口径一致：`<APP_ID>-update*` 是更新暂存（归 `update_helper.sweep_stale_update_dirs` 按 >1h 处理），**不算泄漏**。
- **拍 2** 验证门禁**能报红**（注入标准命名目录 → exit 1）。
- **拍 3** 验证门禁**能报绿**（删掉注入 → exit 0）。
- 任一拍不符即 exit 1——门禁本身坏了（漏报或误报），应修门禁而不是改测试。

## 五、验证时的代码状态提示（重要）

验证期间模板仓又前进到 `988f833`（**门禁豁免收紧**：只认 `mark_staging` 写的标记文件，名字前缀不再豁免，仅作警告）。该提交**尚未拉进 dsh-helper**（子模块仍在 `9e9b123`）。
影响：一旦升级子模块，`temp_gate_selfcheck.py` 的**拍 0**（建一个无前缀标记的 `<APP_ID>-update-selfcheck`）会转为**报红**——自证脚本需同步改用 `mark_staging`，否则会红。**这不是本轮的失败，是下一轮的前置依赖。**
