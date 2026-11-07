# -*- coding: utf-8 -*-
"""dsh 升级助手：版本行 / semver 判据 / 兼容性检查 / 报告 / 记录。

本测试是**纯静态**的：不启动 dsh、不碰真实 ~/.dsh（用 DSH_HOME 指到临时目录）、
不打开浏览器（只测 save_report 落盘，不测 os.startfile）。

实例隔离：<APP>_DATA_DIR / DSH_HOME 必须在第一次 import src 之前设好。
"""
import json
import os
import sys
from pathlib import Path
from _cleanup import rmtree_cleanup, scratch_dir  # noqa: E402

_TMP = scratch_dir("dsh-upgrade-test-")
os.environ["DSH_HELPER_DATA_DIR"] = _TMP
os.environ["DSH_HOME"] = str(Path(_TMP) / "dshhome")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pystray  # noqa: E402
from template import i18n  # noqa: E402
import main as M  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_fake_npm():
    """造一个假 npm 全局目录：dsh.cmd + 包清单（默认版本 0.2.2-rc.1）。"""
    npm = Path(_TMP) / "npm"
    pkg = npm / "node_modules" / "@deepseek-ai" / "dsh"
    write_json(pkg / "package.json", {"name": "@deepseek-ai/dsh", "version": "0.2.2-rc.1"})
    cmd = npm / "dsh.cmd"
    cmd.write_text("@echo off\r\n", encoding="utf-8")
    return npm, cmd


def set_dsh_command(cmd):
    """改配置里的 dsh.cmd 并让版本缓存失效。"""
    M.CFG["dsh_cmd"] = str(cmd) if cmd else ""
    M._DSH_VERSION_CACHE["done"] = False


# ---- 1. 版本行 -------------------------------------------------------------
npm_dir, fake_cmd = make_fake_npm()
set_dsh_command(fake_cmd)
i18n.init("zh")
check("version line shows installed version",
      M.dsh_version_line() == "dsh 版本：0.2.2-rc.1", M.dsh_version_line())

set_dsh_command(None)
check("version line shows not installed when unconfigured",
      M.dsh_version_line() == "dsh 版本：未安装", M.dsh_version_line())

# 包清单存在但读坏 -> 读取失败
pkg = npm_dir / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
pkg.write_text("{not json", encoding="utf-8")
set_dsh_command(fake_cmd)
check("version line shows read failure on malformed manifest",
      M.dsh_version_line() == "dsh 版本：读取失败", M.dsh_version_line())

# 英文界面同一状态
i18n.init("en")
check("version line is English after language switch",
      M.dsh_version_line() == "dsh version: read failed", M.dsh_version_line())
i18n.init("zh")

# ---- 2. semver 范围（真实世界 ranges + npm semver 边界） ---------------------
RANGES = [
    ("0.2.2-rc.1", ">=0.2.0-rc.1 <0.3.0-0", True),
    ("0.2.2-rc.1", "^0.2.2-rc.1", True),
    ("0.3.0-rc.1", "^0.2.2-rc.1", False),          # 上界是 <0.3.0-0
    ("0.2.2-rc.1", "^0.2.2-alpha.2", True),
    ("0.2.2-rc.1", "^0.1.0-rc.7 || ^0.1.1-rc.2 || ^0.1.2-alpha.2 || ^0.2.0-rc.1 || ^0.2.1-alpha.3 || ^0.2.1-rc.1", True),
    ("0.2.2-rc.1", ">=0.1.2-rc.1 <0.2.0 || >=0.1.5-alpha.1 <0.2.0 || >=0.1.6-0 <0.2.0 || >=0.1.7-0 <0.2.0 || >=0.2.0-0 <0.3.0 || >=0.2.1-0 <0.3.0", True),
    ("0.2.2-rc.1", "0.2.2-rc.1", True),
    ("0.2.2-rc.1", "*", True),
    ("0.2.2-rc.1", ">=0.1.0-rc.6 <0.3.0", True),
    ("0.2.2-rc.1", ">=0.2.1-0 <0.3.0", True),
    ("0.2.2-rc.1", "^0.1.5-rc.1", False),
    ("0.2.2-rc.1", ">=0.2.0 <0.3.0", True),
    ("0.2.2-rc.1", ">=0.3.0", False),
    ("0.2.2-rc.1", "~0.2.2", False),       # ~0.2.2 = >=0.2.2 <0.3.0-0，不含 prerelease
    ("0.2.2-rc.1", "~0.2.2-rc.1", True),
    ("0.2.2", ">=0.2.0-rc.1 <0.3.0-0", True),       # release 也命中含 prerelease 的范围
    ("0.2.2-rc.1", "", False),
]
bad_ranges = []
for version, range_text, expected in RANGES:
    got = M.version_in_range(version, range_text)
    if got != expected:
        bad_ranges.append((version, range_text, expected, got))
check("semver range matching matches npm on real-world ranges",
      not bad_ranges, repr(bad_ranges))

check("prerelease precedence: alpha < rc < release",
      M._semver_cmp(M._parse_semver("0.2.2-alpha.2"), M._parse_semver("0.2.2-rc.1")) < 0
      and M._semver_cmp(M._parse_semver("0.2.2-rc.1"), M._parse_semver("0.2.2")) < 0)
check("numeric prerelease identifiers sort before alphanumeric",
      M._semver_cmp(M._parse_semver("0.2.2-1"), M._parse_semver("0.2.2-alpha")) < 0)

# ---- 3. 兼容性检查（假 profile） -------------------------------------------
profile_dir = Path(_TMP) / "dshhome" / "profiles" / "web"
write_json(profile_dir / "package.json", {
    "name": "dsh-profile-web",
    "private": True,
    "dependencies": {
        "dsh-good": "1.0.0",
        "dsh-old": "0.1.0",
        "dsh-hard": "0.5.0",
        "dsh-missing": "0.1.0",
    },
    "dsh": {"profile": {"bundles": ["dsh-good", "@deepseek-ai/dsh-base"]}},
})
write_json(profile_dir / "node_modules" / "dsh-good" / "package.json", {
    "name": "dsh-good", "version": "1.0.0",
    "engines": {"dsh": ">=0.2.0-rc.1 <0.3.0-0"},
})
write_json(profile_dir / "node_modules" / "dsh-old" / "package.json", {
    "name": "dsh-old", "version": "0.1.0",
    "peerDependencies": {"@deepseek-ai/dsh-agent": "^0.1.5-rc.1"},
})
write_json(profile_dir / "node_modules" / "dsh-hard" / "package.json", {
    "name": "dsh-hard", "version": "0.5.0",
    "engines": {"dsh": ">=0.2.0-rc.1 <0.3.0-0"},
    "dependencies": {"@deepseek-ai/dsh-tools": "0.2.2-rc.1"},
})
# dsh-missing: 故意不建 package.json

check("profile dir resolves under DSH_HOME",
      M.dsh_profile_dir("web") == profile_dir, str(M.dsh_profile_dir("web")))

data = M.check_plugin_compatibility("0.2.2-rc.1")
by_name = {p["name"]: p for p in data["plugins"]}
check("official bundle without local manifest is filtered out",
      "@deepseek-ai/dsh-base" not in by_name, repr(sorted(by_name)))
check("good plugin is compatible (engines.dsh covers target)",
      by_name["dsh-good"]["conclusion"] == "compatible"
      and by_name["dsh-good"]["batch"] == "A", repr(by_name["dsh-good"]))
check("old peer line is incompatible",
      by_name["dsh-old"]["conclusion"] == "incompatible"
      and by_name["dsh-old"]["batch"] == "B", repr(by_name["dsh-old"]))
check("hardcoded host dependency is incompatible even when A passes",
      by_name["dsh-hard"]["conclusion"] == "incompatible"
      and by_name["dsh-hard"]["batch"] == "B", repr(by_name["dsh-hard"]))
check("missing manifest is unknown, not a crash",
      by_name["dsh-missing"]["conclusion"] == "unknown"
      and by_name["dsh-missing"]["batch"] == "B", repr(by_name["dsh-missing"]))
check("summary counts add up",
      data["summary"] == {"compatible": 1, "incompatible": 2, "unknown": 1},
      repr(data["summary"]))

# 精确豁免：compatibility.json 命中 name@version + 目标版本 -> 转为适配
write_json(profile_dir / "compatibility.json", {"dsh-old@0.1.0": ["0.2.2-rc.1"]})
data_exempt = M.check_plugin_compatibility("0.2.2-rc.1")
old_exempt = {p["name"]: p for p in data_exempt["plugins"]}["dsh-old"]
check("compatibility.json exemption flips incompatible to compatible",
      old_exempt["conclusion"] == "compatible" and old_exempt["exempted"] is True,
      repr(old_exempt))
(profile_dir / "compatibility.json").unlink()

# 空 profile / 不存在 profile：不崩，返回空列表
empty_dir = Path(_TMP) / "dshhome" / "profiles" / "empty"
write_json(empty_dir / "package.json", {"name": "empty", "dependencies": {}, "dsh": {"profile": {"bundles": []}}})
check("empty profile yields no plugins and no crash",
      M.check_plugin_compatibility("0.2.2-rc.1", profile="empty")["plugins"] == [])
check("missing profile yields no plugins and no crash",
      M.check_plugin_compatibility("0.2.2-rc.1", profile="does-not-exist")["plugins"] == [])

# ---- 4. 报告 HTML ----------------------------------------------------------
html = M.build_report_html(data)
check("report html contains title and summary labels",
      "dsh 升级兼容性报告" in html and "适配" in html and "不适配" in html and "未知" in html)
check("report html lists plugin rows",
      "dsh-good" in html and "dsh-old" in html and "dsh-hard" in html)
check("report html contains batch suggestions",
      "A 批" in html and "B 批" in html)
check("report html escapes plugin names",
      "<script>" not in M.build_report_html({
          "generated_at": "t", "current_version": "c", "target_version": "t2",
          "runtime_version": "", "profile": "web", "profile_dir": "p",
          "plugins": [{"name": "<script>x</script>", "version": "1", "a": True,
                       "a_detail": "", "b": None, "b_detail": "", "c": True,
                       "c_detail": "", "d": None, "d_detail": "",
                       "conclusion": "compatible", "batch": "A",
                       "exempted": False, "detail": ""}],
          "summary": {"compatible": 1, "incompatible": 0, "unknown": 0},
      }))

report_path = M.save_report(html)
check("report is saved under the user data upgrade dir",
      report_path.is_file() and report_path.parent == M.DSH_UPGRADE_REPORT_DIR,
      str(report_path))

# ---- 5. 升级记录 -----------------------------------------------------------
record_path = M.start_upgrade_record("0.2.1-rc.2", "0.2.2-rc.1")
check("record file is created under the records dir",
      record_path.is_file() and record_path.parent == M.DSH_UPGRADE_RECORD_DIR,
      str(record_path))
first_text = record_path.read_text(encoding="utf-8")
check("record header contains current and target versions",
      "0.2.1-rc.2" in first_text and "0.2.2-rc.1" in first_text, first_text[:120])
M.append_record("插件兼容性检查", "- dsh-good@1.0.0: 适配")
appended = record_path.read_text(encoding="utf-8")
check("append does not overwrite the header",
      appended.startswith(first_text) and "dsh-good@1.0.0" in appended)

# ---- 6. 菜单接线 -----------------------------------------------------------
def menu_labels(menu):
    out = []
    for item in menu.items:
        text = item.text
        if callable(text):
            try:
                text = text(item)
            except Exception:
                text = ""
        if text:
            out.append(str(text))
        # pystray 的子菜单是 MenuItem(action=Menu)，经 .submenu 暴露。
        submenu = getattr(item, "submenu", None)
        if submenu is not None:
            out.extend(menu_labels(submenu))
    return out


i18n.init("zh")
labels = menu_labels(M.build_menu())
check("menu contains dsh version line",
      any(item.startswith("dsh 版本：") for item in labels), repr(labels[:4]))
check("menu contains upgrade assistant entry with four actions",
      "dsh 升级助手" in labels and "检查插件兼容性" in labels
      and "打开升级流程文档" in labels and "打开升级记录" in labels
      and "复制升级命令" in labels, repr([x for x in labels if "升级" in x]))

check("temp dir cleaned up (no leak)", rmtree_cleanup(_TMP), str(_TMP))
print("UPGRADE FEATURE TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
