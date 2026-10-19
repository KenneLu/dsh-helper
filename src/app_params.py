# -*- coding: utf-8 -*-
"""dsh-helper 参数卡：kit 出厂默认值由 template.appconfig 提供，本文件覆盖工具差异。

本文件由工具自持（在 src/template 借条之外），template.appconfig 运行时
`try: import app_params` 读取覆盖；未写的键保留 kit 出厂默认值。
VERSION 不入卡：单一事实源在 src/main.py（build.bat / release.yml findstr 读取）。
"""
APP_ID = "dsh-helper"
APP_NAME = "dsh-helper"
AUTOSTART_KEY = APP_NAME
REPO_OWNER = "KenneLu"
REPO_NAME = "dsh-helper"
EXE_NAME = "dsh-helper.exe"

ICON_ASSET = "resources/img/dsh-helper-icon.png"   # G5：手工资产派生双 ico（icons.py 消费）
ICON_DRAW = None

# W6 状态贴图（运行时像素管线退役，链路挪构建期 src/icon_pipeline.py 跑一次）。
# running=暖金提亮 / stopped=灰度——与旧运行时 make_icon_image 逐像素等价。
import icon_pipeline as _ip
ICON_STATE_ARTISTS = {
    "running": lambda base: _ip.state_image(base, running=True),
    "stopped": lambda base: _ip.state_image(base, running=False),
}
