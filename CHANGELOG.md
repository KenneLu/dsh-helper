# Changelog

All notable changes to dsh-helper are documented here.
The tagging convention matches the versions in this file.

## Unreleased
- **Registered: `modules/service_link` is an evaluated, inline-equivalent difference - a form gap, not a capability gap**
  (lead ruling: keep the inline form now, evaluate adoption next round). The template module is a
  single-object ownership state machine (`refresh` / `can_start` / `start` / `stop` / `state` /
  `spectators`) over `NONE -> ADOPTED -> OWNED`. This tool's equivalent exists, but inline and spread
  across `main.py`: `probe_url:354` (probe), `is_dsh_web_process:378` + `existing_dsh_processes:391`
  (adoption candidates), the `managed` flag `:715`, `force_terminate_process_tree:585` and
  `stop_process_group:612` (graceful stop, then `taskkill /F` fallback). What is missing is the
  *shape* - one object, one state - not the behaviour; and the template README itself names this
  tool's "adopt an already-running service" design as one of the module's blueprints, so this is
  provenance rather than a missed copy. Adopting the module means editing the start/stop/quit paths,
  which the user can see, so it is deliberately kept out of the current GUI-verification window.
  **Follow-up obligation (a registration, not an exemption):** next round, either adopt `service_link`
  (copy the file, inject the `probe` / `launch` / `terminate` / `graceful` / `adopt_pid` callbacks) or
  record a verifiable reason why the inline form is better - per B4 principle 3 and the two
  allowed-difference premises.
- **The tray menu is now rebuilt only when something it displays actually changed**
  (E2-09). Every state change used to call `icon.update_menu()` unconditionally, and
  pystray rebuilds by `DestroyMenu` + `CreatePopupMenu` - so a rebuild while the menu
  was open yanked it out from under the cursor (the "loses focus while you slide down
  it" symptom). The tool now feeds a tuple of everything the menu shows into
  `tray_kit.MenuSignature`; the rebuild only happens on a signature change, and when
  the menu is open it is **deferred** to a 1.5 s catch-up thread (`menu_refresh_loop`).
  A `menu_is_open()` probe (the `GUI_INMENUMODE` flag scanned across this process's
  threads, with the `#32768` system-menu window class as a fallback) answers "is it
  open". The signature is written against `build_menu()` item by item - language,
  status/URL text, every `enabled`/`checked` driver - because **a field missing from
  the signature means that field changing never refreshes the menu**.
- Template resync: `modules/log_kit` -> 1.0.3 (`log` is now print-shaped, `def
  log(*parts)`, which is the contract `update_helper`/`tray_kit` have always called it
  with) and `modules/paths/README.md` copied byte-for-byte (the `process_pending_update`
  deprecation notice).
- **All Tk work now goes through one dedicated thread** (E1-03/I-03). The dialogs were
  opened from `threading.Thread` workers (so the tray stays responsive while the user
  types), which meant building and destroying a Tk interpreter **on a worker thread** -
  that works only by accident on some `_tkinter` builds and breaks on others. `ui_post()`
  marshals a callable onto a single long-lived Tk thread and blocks for its result, so
  the only thing crossing a thread boundary is a plain Python object. `tk.Tk()` is now
  reached from exactly one thread in the whole process: the dsh.cmd picker, the quit
  confirm (both the rich dialog and the native fallback), and clipboard access.
  The thread is **started on demand**, not from `main()` - the first version started it
  only in `main()`, and `test_update_chain` (which calls `quit_menu()` without ever
  running `main()`) then **hung forever** on `done.wait()`. `ui_post` also runs inline
  when it is already on the Tk thread, so a re-entrant post cannot self-deadlock.
- Tests: new `tests/test_ui_marshal.py` pins all of that - the result comes back, an
  exception reaches the caller, every call lands on the **same** dedicated thread, a
  re-entrant post resolves, and the thread exists without `main()` ever running. Each
  case runs under a watchdog so a deadlock is reported as a red assertion instead of
  hanging the gate.
- **Test scratch roots moved out of `%TEMP%`; cleanup is now asserted, not assumed**
  (lead rulings R1/R2/R4, after 15 modal Windows Script Host dialogs landed on the
  user's desktop). Two separate things were wrong here:
  * **R2**: all five suites used `tempfile.mkdtemp(prefix="dsh-...")`, so every gate run
    left a directory behind - 36 had accumulated. The cause is the same one l-s2t
    measured: `log_kit`'s `RotatingFileHandler` holds the log file open and Windows
    will not delete a file opened without `FILE_SHARE_DELETE`, while
    `shutil.rmtree(..., ignore_errors=True)` reports that failure as success. Suites now
    use `tests/_cleanup.py` (ported from l-s2t): `scratch_dir()` puts the root under
    `H:\Tools\_verify-scratch\` (env override `DSH_HELPER_SCRATCH_DIR`), and
    `rmtree_cleanup()` drops the log handlers, retries, and **returns False unless the
    directory is really gone** - each suite asserts it, so a future leak turns the gate
    red instead of accumulating silently. Existing leftovers were removed.
  * **R1+R4**: the one place a test really launches an external script now says so
    explicitly - the stand-in `probe.cmd` is created **before** the launch and always
    exists ("was it started" is judged by the **side effect**, a marker file, never by
    the target being absent: `start` on a missing target pops a *modal* box), and the
    wait is bounded with the elapsed time reported. A timeout gets its own red
    assertion naming the first suspect - **a modal box**, not a slow script.
- **The `dsh.cmd` validation timeout now has real headroom, measured rather than guessed**
  (lead ruling: sweep the family for "run a Node CLI to probe capability" paths and adjust
  anything with less than 3x headroom). Eight consecutive timings of `dsh.cmd web --help`
  on this machine: **9.2 / 9.8 / 9.8 / 10.6 / 10.8 / 10.9 / 10.9 / 11.7 s** (median ~10.7 s).
  The 10 s it used to have sat *inside* that spread, which is why the frozen smoke flickered;
  the 30 s it was briefly raised to is only 2.6x the worst case. Now **45 s** (3.9x), which
  also covers the cold-boot branch we cannot measure warm.
- **The family sweep result: dsh is the only tool with that shape.** `opencodex-helper`
  resolves `ocx.cmd` but never runs a capability probe (`run_ocx` calls `start`/`stop`/
  `restart` with 90/60/180 s); `local-speak2text` and `reme-helper` have no Node CLI at all -
  their timeouts are on HTTP or long-running service ops, a different class.
- **Test stand-ins now copy the production signature exactly** (lead ruling J-坑: a
  stand-in more permissive than the real thing manufactures a false green). Two stubs in
  `test_startup_path.py` were written `lambda *a, **k: ...` while the templates declare
  `sweep_stale_update_dirs(max_age=3600.0)` and `pop_failed_update_note(update_dir,
  log=...)` - so a call with the *wrong* arguments would have been silently accepted, the
  exact shape that hid the whole `log` contract conflict. They now spell the real
  parameters out; strictness may cost a false red, which is safe.

Version number is intentionally NOT bumped: as of 2026-09-19 the owner ruled
that dev work lands as local commits only and the version changes only when a
release is cut (STANDARDS "发版节奏" clause 7). The 1.8.2 bump made earlier in
this batch was rolled back to 1.8.1.

- **`ICON_ASSET` had a second source here as well.** `main.py` re-typed the resource
  literal (`resource_path("resources/img/dsh-helper-icon.png")`) while
  `appconfig.ICON_ASSET` already declares that same relative path - and `icons.py` reads
  the *appconfig* one at build time. Two copies of one fact means renaming the asset in
  `appconfig.py` would move the build-time `.ico` while the runtime tray icon kept
  looking for the old name, silently, in both dev and frozen builds. `main.py` now
  resolves `appconfig.ICON_ASSET` instead of repeating it, and the test asserts the
  derivation (`ICON_ASSET.endswith(appconfig.ICON_ASSET)`).
  (Same family as the `APP_DIR` fix below: one derived quantity, one source.)
- **The `log` contract mismatch that made the whole update path unusable.** Found by
  the real failed-marker end-to-end run below, not by reading: the template modules call
  `log` in **print form** - `update_helper` does it in six places, up to five arguments
  (`log("update staged:", staged, "->", target, ...)`) - while this tool's `log()` took
  exactly one. Passing it in as `log=log` therefore raised `TypeError` on the first log
  line of every real path: "downloading" (every download), "pending update launched"
  (every successful stage-and-quit), and "previous update failed" (whenever a failure
  marker exists). Stubs hid it completely - `test_update_chain.py` stubs
  `download_and_prepare`, and the one place that called `launch_pending_cmd` for real
  passed its own `lambda *a: None`. `log()` now takes `*parts` and joins them, matching
  the contract the modules are written against.
  The same mismatch exists in the template's own `log_kit.make_logger` (it returns
  `log(message)`), which is why l-s2t/reme have the same latent shape - reported for
  the template to settle, since that file is not ours.
- Tests: `test_update_chain.py` pins the arity contract directly
  (`log("contract", "check", "with", "five", "args")` must not raise), and
  `test_startup_path.py` now finishes with a **real** `update.failed` marker and the
  **real** `pop_failed_update_note`: it asserts the user is notified, the marker is
  consumed, and a second start stays silent. That run is what caught the defect.

- **Quitting with an update staged no longer flashes a console window.** The exit path
  used `os.system('start "" /min "<script>"')`, which goes through `cmd` and creates a
  console for a GUI process that has none - a black box blinks on the desktop as the
  tray exits - and interpolated the path into a shell string. It now calls
  `update_helper.launch_pending_cmd()`, which launches `cmd /c <script>` with
  `CREATE_NO_WINDOW | DETACHED_PROCESS` so the script outlives the parent and completes
  the swap invisibly (the reme form). This was also the last `MUST-WIRE` symbol the
  module README declared, so conformance **C-27** ("adoption = copy + wire", new
  template check) now reports 3/3 instead of failing on this repo.
- Tests: `test_update_chain.py` pins the new form and would go red if the old one came
  back - it asserts `launch_pending_cmd` receives the stored path, asserts `os.system`
  is **not** called, reads the `creationflags` actually handed to the kernel
  (`134217736` = `CREATE_NO_WINDOW | DETACHED_PROCESS`), and finally runs a real
  throwaway `.cmd` to prove the child survives the parent and writes its marker.
- Template resync: `modules/i18n` -> 2.2.0 (`LANG` is no longer a rebindable module
  global; it is derived from an internal `_STATE`, so `from .i18n import *` cannot copy
  it - the shape behind the stale-menu bug is now structurally impossible). The call
  sites already used `current_lang()`, so nothing changed.

- **`APP_DIR` now has exactly one source** (`paths.APP_DIR`). `main.py` used to derive
  its own copy - right when frozen (the exe dir) but `src/` in dev, where `paths` says
  the repo root. The two only had to agree in the packaged build, so the split stayed
  invisible: in dev `ICON_ASSET` resolved to `src/resources/img/...`, which does not
  exist, and `_load_icon_base()` silently fell back to the hand-drawn placeholder while
  every gate stayed green. The local definition is gone and `APP_DIR` is imported from
  `modules.paths` with the rest of the paths; `resources/` lives at the repo root, which
  is also what `--add-data` bundles.
- **The frozen smoke's `dsh.cmd` validation no longer flips on timing.**
  `validate_dsh_command` allowed 10 s for `dsh.cmd web --help`, but a Node cold start
  with its plugins takes **7.7-11.0 s** on this machine (three consecutive
  measurements), so the gate landed mid-range and went red at random while reporting
  only "validation failed" - which reads as a broken environment rather than a tight
  timeout. Now a named `DSH_CMD_VALIDATE_TIMEOUT_SEC = 30`. Validation already runs on a
  background thread behind a "validating..." notification, so the extra headroom costs
  only patience.
- Tests: `test_startup_path.py` pins both halves of the `APP_DIR` fix - it is the same
  object as `modules.paths.APP_DIR`, and the icon asset really is a file in dev mode.
- Template resync: `modules/update_helper` -> 1.4.2 (1.4.1 made `:stage_invalid` preserve
  the scene like `:install_failed`; 1.4.2 guards the third `start` - the one after a
  restore - because "the restore did not error" is not "the exe is back"). `.py` and
  `README.md` copied; the `.py` header re-stamped with the new TEMPLATE-VER.

- Update housekeeping is now actually wired in (T4 收尾): `sweep_stale_update_dirs()`
  runs at startup and removes `<APP_ID>-update-*` staging dirs that an interrupted
  updater left in %TEMP% (only those older than 1 h, so an in-flight update is never
  touched - ~50 MB per run otherwise accumulates forever); `pop_failed_update_note(
  UPDATE_DIR)` then consumes the failed-update marker **once** and, when it was set,
  the tray raises `notify_update_failed_prev` the moment it becomes visible. Both were
  dead template code before (0 call sites in `src/*.py`). The string the module returns
  is Chinese, so only its truthiness is used and the user-visible sentence comes from
  the locale table (T1); the detail is already in `update.log`. `--smoke` returns before
  `main()`, so the probe stays read-only (D3-03).
- Tests: `test_startup_path.py` now covers all three acceptance points - the **real**
  sweep deletes an aged dir while keeping a fresh one and an unrelated one (with
  `tempfile.tempdir` redirected to a throwaway root), the **real** marker is reported
  once and is silently gone on a second start, and `main()` calls sweep-then-note and
  surfaces the i18n message.
- Template resync: `modules/tray_kit` -> 2.2.0 - `mutex_name_ok()` is now the single
  shape predicate shared by the guard, the probe and `single_instance_free()`; the
  guard passes an illegal name **through** with a log line instead of failing closed
  (D3.2), leaving the "go red" job to the build-time probe (D3.3). Call sites unchanged;
  the module `README.md` was resynced byte-for-byte as well.

- Updates (T4): the update path no longer reads `update_helper`'s mutable
  globals. The discovered version is cached in `LATEST_VERSION` (taken from
  `check_update()`'s return value), `download_and_prepare()`'s returned script
  path is stored in `PENDING_UPDATE_CMD`, the "Download and update" item is
  enabled from that cache, and the quit path launches the stored script. The
  template package's `import *` had copied `PENDING_CMD` (so `apply.cmd` was
  never launched) and `UPDATE_READY` was never assigned (item grey forever);
  keying off return values instead makes this tool independent of that state
  (verified: `grep update_helper.(PENDING_CMD|UPDATE_READY)` is empty).
  Also resynced `modules/update_helper` to template 1.3.0.
- **A real bug the new tests caught on their first run** (not a fix prepared in
  advance): `check_update_menu` declared `global LATEST_VERSION`, but the
  assignment lives in the nested `worker()` - a `global` in the enclosing
  function does not reach into a nested one, so the write bound a local and the
  module cache stayed `None`. The "Download and update" item would have stayed
  grey forever even when a newer version was found. The declaration was moved
  into `worker()`. This is the concrete payoff of the "run a real execution"
  rule: the defect is invisible to reading, and neither the update-path review
  nor the frozen smoke would have caught it - `test_update_chain.py` caught it
  on its first run.
- Tests (D1 1.4/1.5): added `tests/` with four suites, all isolated (each pins
  its own `DSH_HELPER_DATA_DIR` before importing main; no mutex, no registry,
  no network) and wired into build.bat as a gate:
  - `test_single_instance.py` - mutex name matches the family derivation, the
    kernel accepts it, the old illegal name still fails, acquire/refuse uses a
    test-only name (SINGLE-08), and the guard fails open.
  - `test_startup_path.py` - runs the real `main()` with heavy stubs and asserts
    the startup sequence is reached (log `startup`, `migrate_autostart`, command
    detection).
  - `test_i18n_menu.py` - switching language reads via the package namespace and
    really rebuilds the menu into English (the 2.1.1 regression).
  - `test_update_chain.py` - stubbed update chain: the discovered version is
    cached, the returned apply-script path is stored, and quit launches it.
- Frozen smoke now **dual-pins** `DSH_HELPER_DATA_DIR` + `DSH_HELPER_CONFIG`
  (the config is a throwaway copy inside smoke-data, so the shipped config is
  never rewritten) - D1 unified convention.
- Single-instance guard probe (D3.1/C-10): `--smoke` now calls
  `tray_kit.mutex_name_is_valid(APP_ID)`, so an illegal mutex name fails the
  build loudly - that silent failure once kept a sibling tool dead for months
  while every gate stayed green. `tests/test_single_instance.py` asserts the
  probe both ways. Resynced `modules/tray_kit` to 2.1.0, and `build.bat`'s
  VERSION parse switched to the D1 two-step form (a trailing comment can no
  longer leak into the release path).
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
