# tests/ 约定（写测试前先读这一页）

这一页只写**本仓已经付过代价**的规矩。每条都附本仓或本家族的真实史，不是通用建议。

## 怎么跑

- `build.bat` 用 `for %%t in (tests\test_*.py)` 把**每个** `test_*.py` 当门禁跑，失败即 `exit /b 1`；
  `_cleanup.py` 不匹配该 glob，是共享助手。
- 单独跑：`H:\Tools\Python\Python313\python.exe tests\test_quit_fail_open.py`。
  这些是**脚本式**测试（末尾 `sys.exit(0/1)`），不是 pytest 用例。

## 硬规矩

### 1. 桩必须照抄生产签名——不宽，**也不窄**

- **太宽**：更宽松的替身会掩盖契约冲突（"桩上通过、生产里 `TypeError`"）。J 坑原文就是这句。
- **太窄**：本仓实测——`state_copy` 曾被换成裸 dict，结果 `_menu_signature()` 去读
  `state["phase"]` 直接 `KeyError`，**测试崩在实现内部的无关位置**，看起来像实现坏了。
  改为在**真实** `state_copy()` 的结果上加键。
- 照抄形式：生产是 `def stop_process_group(pids, graceful=True)`，桩就写
  `lambda pids, graceful=True: ...`，不要写成 `lambda pids: ...`。

### 2. 断言必须有对照，否则**恒真**

- 要断言"链路不可用时不得停服务"，必须先**注入**假目标 / 假 pid：否则 `CFG["targets"]` 为空，
  `kill_target_procs` 永远不被调用，那条断言**恒真**——它证明的是"没有目标"，不是"没停服务"。
- 三条分支各要有一例：链路不可用 / 用户明确取消 / 用户确认真的是。
  少了任何一例，"一律退出"或"一律不退出"都能通过。

### 3. "能红"是断言的一部分

- **只加断言不验它会红 = 没加。** 本仓做法：`git stash push -- src/main.py` → 跑 → 必须红，
  且红在**预期的那几条**上 → `git stash pop` → 与事先保存的副本 `diff` **逐字节一致**
  （后半句证明"红完没把代码弄坏"，缺了它只完成了一半）。
- 把红态输出里**旧的错误行为原文**留在提交信息里。例：`proceeding without confirmation`
  紧跟着 `quit cancelled by user`——**矛盾本身就是证据**，比任何断言描述都直观。

### 4. 实例隔离（F11/D12）

- `<APP>_DATA_DIR` / `<APP>_CONFIG` 必须在**第一次 import `src`** 之前设好；import 期就有落盘副作用。
- 碰注册表的测试必须**备份 → 操作 → 还原 → 回读断言**（还原本身要断言，不能"尽力而为"）。

### 5. 清理要可验证

- `shutil.rmtree(..., ignore_errors=True)` 会把"删不掉"变成"看起来成功"。
- 用 `_cleanup.rmtree_cleanup()`：先放日志句柄（`RotatingFileHandler` 会占住日志文件）、再删、
  **删后回读**；删不掉即**红**。跑完 `%TEMP%` / scratch 里留目录就是缺陷，不是噪声。

### 6. 不碰用户的真实实例

- 不启停真实服务、不 `taskkill`、不碰别的进程。测试要能在**无实例**与**有实例**两种状态下都通过；
  确实做不到的，在文件头写明前提，而不是把断言削弱到"恰好能过"。

### 7. 死锁要变成红灯，不是挂死

- 跨线程 / 跨进程的等待一律用带超时的守护线程包一层（见 `test_ui_marshal._guard`）。
  **测试挂死比测试失败更糟**：CI 上它不报错，只是永远不结束。
