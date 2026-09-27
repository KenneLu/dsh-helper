# T1 · appconfig —— 参数区（拷贝后唯一要改的文件）

> 规范出处：STANDARDS.md §F2「单一真源」、执行文档 D6/D15。
> 蓝本：reme-helper 的常量区 + local-speak2text/paths.py 的字段集。

## 定位

工具之间的全部差异收敛到一个文件。其余模板文件拷贝后**零修改**（sync_check 按
字节比对，见 `my-diy-tool-template/sync_check.py`）。这是"拷贝式模板 + 稳定接口"的基石：
模板升级时你只 diff 这个文件。

## 对外接口（稳定承诺）

| 名称 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `APP_ID` | str | ✅ | 机器标识：数据目录名/互斥体/日志文件名/更新包名前缀。小写连字符 |
| `APP_NAME` | str | ✅ | 展示名（托盘 title、弹窗标题），可中文 |
| `REPO_OWNER` / `REPO_NAME` | str | 更新器需要 | GitHub `owner/repo`，T4 update_helper 消费 |
| `EXE_NAME` | str | 更新器需要 | 打包产物 exe 名。**不带版本号**（B3：自启注册表存完整路径） |
| `ICON_DRAW(size)` | callable | T6 二选一 | 图标绘制函数：**单参** size → `PIL.Image`（T6 `icons.py` 以 `ICON_DRAW(256)` 调用），工具唯一的"个性" |
| `ICON_ASSET` | str \| None | T6 二选一 | 手工 png 路径（相对仓库根）；非 None 时**优先于** `ICON_DRAW`。与 `ICON_DRAW` 至少给一个（G5） |
| `VERSION` | str | 按工具 | 三段 semver（D15）。**允许不放这里**——只要全工具只有一个定义处即可（如 dsh-helper 放 main.py，CI findstr 读取） |

## 采纳步骤

1. 拷 `appconfig.py` 到工具根目录，改上表字段；
2. 其余模板件按需拷入（它们都 `from appconfig import ...`）；
3. **不要**给 appconfig.py 加 TEMPLATE-FROM 头——它是参数文件，sync_check 自动豁免比对（`[param]`）。

## 边界与坑

- 业务密码/密钥**永不入内**（F2：凭据单独文件且不入库）。
- 一个字段两处定义 = 迟早漂移；新增参数先问"是不是所有工具都该有"，是 → 改模板与本文档；否 → 放工具自己的配置。
