# icons —— 代码生成双 ico

> 蓝本：local-speak2text/icons.py + reme-helper 的多尺寸帧表。

## 定位

图标由代码绘制（`appconfig.ICON_DRAW`），构建时生成 ico 喂给 PyInstaller——
仓库不进二进制图标资源，改图标 = 改一个函数。

## 对外接口（稳定承诺）

| 名称 | 说明 |
|---|---|
| `base_image()` | 图形来源二选一：`ICON_ASSET`（相对仓库根的 png，统一缩放到 256 基图）优先，否则 `ICON_DRAW(256)` |
| `make_icons(base_dir)` | 在 `base_dir` 下生成 `<APP_ID>.ico`（托盘态，`TRAY_SIZES`）+ `<APP_ID>-taskbar.ico`（任务栏态，`TASKBAR_SIZES`），返回两路径 |
| `TRAY_SIZES` / `TASKBAR_SIZES` | 帧档常量（6 档 / 15 档） |
| CLI | `python src/icons.py`（dev 态 ico 落仓库根） |

> 2026-09-19 更正：本表曾写 `make_image(size=64)` / `write_app_icons(out_dir=".")` / `--out`，
> 与正本 `icons.py` 2.0.0 不符（§4.1.4）。**接口以正本为准**。

## 采纳步骤

1. `appconfig.ICON_DRAW` 画出本工具图形（状态色与运行时托盘绘制同语言）；
2. 构建前跑 `python icons.py`，PyInstaller `--icon <APP_ID>-taskbar.ico`；
3. 交付物检查断言 ico 在发布包里。

## 边界与坑

- 任务栏 ico 要铺满真实索取的像素档（reme-helper 用 15 档覆盖 100%–200% DPI），缺档被外壳缩放会发糊；
- 运行态指示（如 蓝=空闲/橙=录制）与 ico 默认态同一设计语言。
