# Changelog

All notable changes to dsh-helper are documented here.
The tagging convention matches the versions in this file.

## Unreleased

Version number is intentionally NOT bumped: as of 2026-09-19 the owner ruled
that dev work lands as local commits only and the version changes only when a
release is cut (STANDARDS "发版节奏" clause 7). The 1.8.2 bump made earlier in
this batch was rolled back to 1.8.1.

- Updates (T4): the update path no longer reads `update_helper`'s mutable
  globals. The discovered version is cached in `LATEST_VERSION` (taken from
  `check_update()`'s return value), `download_and_prepare()`'s returned script
  path is stored in `PENDING_UPDATE_CMD`, the "Download and update" item is
  enabled from that cache, and the quit path launches the stored script. The
  template package's `import *` had copied `PENDING_CMD` (so `apply.cmd` was
  never launched) and `UPDATE_READY` was never assigned (item grey forever);
  keying off return values instead makes this tool independent of that state
  (verified: `grep update_helper.(PENDING_CMD|UPDATE_READY)` is empty).
  Also resynced `modules/update_helper` to template 1.2.0.
- i18n / T1 (bilingual UI): adopted the template `modules/i18n` 2.1.1
  (light form) - `locales/zh.json` (base) + `locales/en.json`, flat KV,
  en falls back to zh. Every tray menu label, notification, dialog, status
  line and validation/error message goes through `i18n.t()`; data (paths,
  service name, `dsh.cmd`) is not translated. A "Language / 语言" item sits in
  the preferences section; switching re-inits the language, persists it to
  `config.json` (`language`), and **rebuilds the menu explicitly** - reading
  the current language via `i18n.current_lang()` (not `i18n.LANG`, whose
  package re-export was a stale-copy trap fixed in 2.1.1).
- Build gate: `py_compile` list now includes `modules/i18n/i18n.py`; a new
  gate asserts the zh/en tables both answer the core keys (D1-04); a new
  `--lang-audit` gate statically scans `src/main.py` for Chinese literals
  that are outside the zh table (AST-based, docstrings excluded) - currently
  **0 untranslated**. PyInstaller bundles `locales` via `--add-data`.
- Autostart (G4.1): the inline Run-key code was replaced by the family
  template module `modules/autostart` (T3, template 1.1.1, byte-identical
  copy; sync_check gates it). Packaged builds now prefer the stable install
  location (`%LOCALAPPDATA%\dsh-helper\app\dsh-helper.exe`) when it exists
  and fall back to the current exe otherwise.
- Autostart self-heal (G4.1-3/5): `migrate_autostart()` runs at startup and
  silently rewrites a Run value whose exe no longer exists (old package
  folder deleted) - verified against a seeded dead link on the frozen build.
  When the value is absent the call is a strict no-op: it never creates a
  new autostart entry.
- Single source of truth (F2-01): the `autostart` key was removed from
  `config.json`; the registry value is now the only state, so the config and
  the Run key can no longer disagree.
- Build gate: `src\modules\autostart\autostart.py` added to the py_compile
  step; `paths.py`/`tray_kit.py` copies resynced to template 1.1.3 / 2.0.2
  (mechanism-only, call sites unchanged).
- Honest limitation: **the full stable-install mechanism (G4.1-1) is still
  not implemented** - nothing installs a build into `...\app\`, so on a
  machine without that folder autostart falls back to the versioned
  `release\dsh-helper-<version>\` path and a folder change can still strand
  it. Tracked in the README "known gaps" section.

## 1.8.1

- Internal structure: family template modules now live under
  `modules/` (imports via `from modules import ...`); sync_check and CI
  compile lists updated.
- Icons (G5): tray/taskbar/exe icons are now derived at build time from
  `resources/img/dsh-helper-icon.png` via `src/icons.py` (dual ico, 15-frame
  taskbar table for 100%-200% DPI) - the exe no longer ships the default
  PyInstaller icon.
- Quit (G4.1-4/G4.2-5): exiting the tray now asks for confirmation (red
  confirm button, cancel has default focus, Esc/close = cancel). A persistent
  "also stop the dsh service" checkbox (default off - dsh is released and
  keeps running) is saved the moment it is toggled. Previously quit silently
  killed the managed dsh.
- Build: release layout is now `release\dsh-helper-<ver>\` (exe carries no
  version); frozen smoke + deliverable checks are part of the gate; the CI
  release workflow runs the same build.bat chain (smoke skipped on CI - no
  dsh.cmd on runners); the smoke run's data root is redirected so builds
  never touch the developer's live config/log.

## 1.8.0

- Internal refactor (no behavior change): config/log/update paths now come from
  a `paths.py` module, rotating logging via `log_kit.py`, and the single-instance
  guard / URL masking via `tray_kit.py` - all byte-identical copies of the family
  template modules (my-diy-tool-template), verified by the build's sync_check gate.
- Bilingual README (baseline 8): `README.md` is now the English canonical
  version with `README.zh-CN.md` as the Chinese one, language switch lines on
  top of both; stale 1.6-era menu names and packaging paths refreshed. Both
  files now ship inside the release zip.

## 1.7.0

- Online update: "检查 dsh-helper 更新 / 下载并更新 dsh-helper" tray items backed
  by GitHub Releases (startup auto-check + 24h throttle, zip + sha256 verified,
  applied after tray quit via a one-shot robocopy script that restarts the exe).
- User data moved to `%LOCALAPPDATA%\dsh-helper\` (config.json + log\); the old
  exe-side config.json is migrated once on first run. The exe directory can now
  be replaced wholesale by the updater.
- Rotating log (1 MB × 3 backups, ~4 MB ceiling) instead of an unbounded file.
- Panel URL in the tray menu now masks the token (`token=••••••`); "复制面板地址"
  sits directly under the URL line and copies the full URL - the only way to get
  the token.
- Tray menu restructured to the house standard (read-only info header, updates,
  default entry = open panel, service control, business, open, preferences,
  quit last).
- Repository baseline: git/CI (tests + tag-triggered release), LICENSE,
  CHANGELOG, .gitignore; version now follows semver (single source in main.py).

## 1.6

- Initial public state: tray management for `dsh web` (start/stop/restart,
  panel, URL copy, status refresh), single-instance mutex, autostart with
  command-line match display, timestamped PyInstaller packages.
