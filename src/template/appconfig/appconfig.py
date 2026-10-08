# -*- coding: utf-8 -*-
# TEMPLATE-FROM: my-diy-tool-template/template/appconfig/appconfig.py | TEMPLATE-VER: 1.0.0
# dsh-helper 参数区（appconfig：拷贝后唯一允许修改的文件）。
# VERSION 不在此处：单一事实源在 main.py（build.bat / release.yml findstr 读取）。
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

