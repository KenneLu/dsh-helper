# -*- coding: utf-8 -*-
"""i18n 字面键存在性（新判据，2026-09-19 lead 批准独立挂）。

**为什么需要**：`i18n.t("<缺键>")` **返回键名本身、不抛异常**（实测），而构建的
i18n 门禁只查 9 个核心键 ⇒ "漏加一个键"会**静默**把 `quit_confirm_title` 这样的
键名当文案显示给用户，且门禁全绿。本判据把这一类从"不可见"变成"红"。

**判据**：扫 `src/**/*.py` 里所有 `i18n.t("字面键")`，每个键必须 ∈ en 且 ∈ zh。

**判别力自校（缺了它，"0 缺失"没有意义——匹配器太宽或扫描面为空都会给出 0）**：
  ① **非空**：扫描面必须真的扫到键（否则是"没扫到"而不是"没有"）；
  ② **已知会红**：把一个合成键喂给同一函数，必须被抓到；
  ③ **负对照**：真实键必须不被抓。
三条都过，才允许把"真实扫描 0 缺失"当结论。
"""
import ast
import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
LOCALES = Path(__file__).resolve().parents[1] / "locales"
FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + " " + name + ("  " + detail if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


T_KEY = re.compile(r"\bt\(\s*[\"']([^\"']+)[\"']\s*\)")


def literal_keys(root: Path):
    """扫 root 下所有 .py，返回 {键: 首个出现位置}。"""
    found = {}
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "t" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.setdefault(node.args[0].value,
                                 "%s:%d" % (py.relative_to(root.parent).as_posix(), node.lineno))
    return found


def missing(keys, en, zh):
    return sorted(k for k in keys if k not in en or k not in zh)


en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
zh = json.loads((LOCALES / "zh.json").read_text(encoding="utf-8"))
keys = literal_keys(SRC)

# ---- ① 非空（拒绝空真）----
check("scan surface is non-empty (a empty surface would fake a pass)", len(keys) > 50,
      "keys=%d" % len(keys))

# ---- ② 已知会红：合成的缺键必须被抓到 ----
_probe = {"__nonexistent_key_for_control__": "probe"}
check("known-bad control is caught by the same rule",
      missing(_probe, en, zh) == ["__nonexistent_key_for_control__"],
      repr(missing(_probe, en, zh)))

# ---- ③ 负对照 + 真实扫描 ----
sample = sorted(keys)[0]
check("known-good control is NOT caught", missing({sample: ""}, en, zh) == [], sample)

bad = missing(keys, en, zh)
for k in bad:
    print("      MISSING KEY %s   (%s)" % (k, keys[k]))
check("every i18n.t() literal key exists in BOTH tables", not bad,
      "missing=%s" % (bad or "none"))

# ---- 两张表的键集合应当一致（缺一边也是漏翻）----
only_en = sorted(set(en) - set(zh))
only_zh = sorted(set(zh) - set(en))
check("en/zh key sets are identical", not only_en and not only_zh,
      "only_en=%s only_zh=%s" % (only_en[:5], only_zh[:5]))

print("I18N KEYS TEST " + ("FAILED: " + ",".join(FAILS) if FAILS else "OK"), flush=True)
sys.exit(1 if FAILS else 0)
