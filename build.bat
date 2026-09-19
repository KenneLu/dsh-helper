@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem dsh-helper build: gates -> PyInstaller -> release\<name>-<ver>\ -> frozen smoke
rem ASCII-only: cmd.exe parses .bat with the machine ANSI code page.
rem Usage: build.bat [norun] [nopause] [nosmoke]
rem   norun    do not start the built exe (starting it is the default)
rem   nopause  unattended (no "press any key") - used by CI
rem   nosmoke  skip the frozen smoke gate (CI: runners have no dsh.cmd;
rem            local builds keep the smoke gate ON)
rem ---------------------------------------------------------------------------

set RUN_AFTER=1
set NOPAUSE=
set NOSMOKE=
set BUILD_ARGS=%*
if not defined BUILD_ARGS goto :args_done
for %%a in (%BUILD_ARGS%) do (
  if /i "%%a"=="norun" set RUN_AFTER=
  if /i "%%a"=="nopause" set NOPAUSE=1
  if /i "%%a"=="nosmoke" set NOSMOKE=1
)
:args_done

set PY=H:\Tools\Python\Python313\python.exe
if not exist "%PY%" set PY=python
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Set PY=... at the top of build.bat.
  if not defined NOPAUSE pause
  exit /b 1
)

rem Version single source of truth: VERSION in src\main.py (D15/D16).
rem Two-step parse (D1 0.1): split on '=', then keep the first whitespace-delimited
rem token. A trailing comment on the VERSION line would otherwise be swallowed into
rem the version string by the old "tokens=2,*" form and mangle the release path.
set VERSION=
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"VERSION = " src\main.py') do set VERSION=%%a
for /f "tokens=1" %%a in ("%VERSION:"=%") do set VERSION=%%a
if not defined VERSION (
  echo [ERROR] Cannot read VERSION from src\main.py.
  if not defined NOPAUSE pause
  exit /b 1
)
set VERSION=%VERSION:"=%
set APPNAME=dsh-helper
set RELEASE_DIR=release\%APPNAME%-%VERSION%
set FROZEN_EXE=%RELEASE_DIR%\%APPNAME%.exe
rem The exe must NOT carry the version (B3): autostart and the update chain
rem reference a stable name; the release folder and zip name carry the version.

echo [VERSION] %VERSION%  release: %RELEASE_DIR%

if exist "%RELEASE_DIR%" (
  echo [ERROR] %RELEASE_DIR% already exists. Delete it or bump VERSION.
  if not defined NOPAUSE pause
  exit /b 1
)

rem Running-instance guard (D1-02 refined 2026-09-19): refuse ONLY when the live
rem instance runs FROM THE TARGET release dir. Building a DIFFERENT version dir is
rem safe - files differ, and the frozen smoke pins DSH_HELPER_DATA_DIR and never
rem takes the mutex. What IS unsafe is deleting/overwriting the dir a live instance
rem runs from. The same guard must precede any manual rm of a release dir.
set "RUNNING_EXE="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-Process -Name %APPNAME% -ErrorAction SilentlyContinue).Path | Select-Object -First 1"`) do set "RUNNING_EXE=%%p"
set "RUNNING_DIR="
if defined RUNNING_EXE for %%d in ("%RUNNING_EXE%") do set "RUNNING_DIR=%%~dpd"
rem Normalize a trailing backslash WITHOUT the "last-char == backslash" string
rem test: cmd parses the backslash-quote sequence specially and aborts the whole
rem script with "The syntax of the command is incorrect." -- and it fires on the
rem "no instance running" path (RUNNING_DIR undefined), i.e. the guard would break
rem the very build it exists to protect. Measured with a probe that clears the
rem variable: it aborts inside this block. The for-loop + tilde-fd form strips the
rem trailing backslash for us (same idiom as the TARGET_DIR line below).
if defined RUNNING_DIR for %%d in ("%RUNNING_DIR%") do set "RUNNING_DIR=%%~fd"
set "TARGET_DIR="
for %%d in ("%CD%\%RELEASE_DIR%") do set "TARGET_DIR=%%~fd"
if defined RUNNING_DIR if /i "%RUNNING_DIR%"=="%TARGET_DIR%" (
  echo [ERROR] A %APPNAME% instance is running FROM %RELEASE_DIR%.
  echo [ERROR] Exit it from the tray before building that directory.
  if not defined NOPAUSE pause
  exit /b 1
)
if defined RUNNING_DIR echo [INFO] %APPNAME% running from "%RUNNING_DIR%" - not the target dir, build continues.

echo [GATE] py_compile src\main.py + src\icons.py + src\modules ...
"%PY%" -m py_compile src\main.py src\icons.py src\modules\appconfig\appconfig.py src\modules\autostart\autostart.py src\modules\i18n\i18n.py src\modules\update_helper\update_helper.py src\modules\paths\paths.py src\modules\log_kit\log_kit.py src\modules\tray_kit\tray_kit.py
if errorlevel 1 (
  echo [ERROR] compile gate failed.
  if not defined NOPAUSE pause
  exit /b 1
)

rem i18n gate (T1/D1-04): the locales must answer the core keys in BOTH languages,
rem zh is the base table and en must not silently fall back for menu/nav keys.
echo [GATE] i18n key coverage ...
"%PY%" -c "import json,sys; zh=json.load(open(r'locales/zh.json',encoding='utf-8')); en=json.load(open(r'locales/en.json',encoding='utf-8')); keys=['menu_quit','menu_autostart','menu_language','menu_start','menu_stop','menu_restart','menu_open_logs','notify_stopped','status_running']; miss=[k for k in keys if not zh.get(k) or not en.get(k)]; print('i18n core keys:',len(keys),'missing:',miss); sys.exit(1 if miss else 0)"
if errorlevel 1 (
  echo [ERROR] i18n gate failed: zh/en core keys missing.
  if not defined NOPAUSE pause
  exit /b 1
)

rem lang-audit gate (T5): no user-visible Chinese literal may bypass the zh table.
rem Redirect the data root so the audit's module import never touches the developer's
rem live %LOCALAPPDATA% config/log (F11/D12).
echo [GATE] lang-audit ...
set "DSH_HELPER_DATA_DIR=%CD%\build\lang-audit-data"
"%PY%" src\main.py --lang-audit
set "AUDIT_RC=%errorlevel%"
set "DSH_HELPER_DATA_DIR="
if exist "build\lang-audit-data" rmdir /s /q "build\lang-audit-data" >nul 2>nul
if not "%AUDIT_RC%"=="0" (
  echo [ERROR] lang-audit failed: Chinese literals outside the zh table - see list above.
  if not defined NOPAUSE pause
  exit /b 1
)

rem tests/ suite (D1 1.5): update-chain regression, fully stubbed. Each test pins
rem its own <APP>_DATA_DIR before importing main, and takes no mutex / writes no registry.
echo [TEST] tests suite ...
for %%t in (tests\test_*.py) do (
  "%PY%" "%%t"
  if errorlevel 1 (
    echo [ERROR] test failed: %%t
    if not defined NOPAUSE pause
    exit /b 1
  )
)

rem sync_check gate: the template repo only exists on dev machines (CI checks
rem out a single repo) - skipped there like nosmoke, local builds keep it ON.
if not exist "..\my-diy-tool-template\sync_check.py" goto :sync_skip
echo [GATE] template sync check ...
"%PY%" ..\my-diy-tool-template\sync_check.py --roots dsh-helper
if errorlevel 1 goto :sync_fail
goto :sync_done
:sync_skip
echo [SKIP] template sync check: my-diy-tool-template not present (CI single-repo checkout)
goto :sync_done
:sync_fail
echo [ERROR] template drift detected. See my-diy-tool-template/sync_check.py output above.
if not defined NOPAUSE pause
exit /b 1
:sync_done

echo [BUILD] icon ...
"%PY%" src\icons.py
if errorlevel 1 (
  echo [ERROR] icon generation failed.
  if not defined NOPAUSE pause
  exit /b 1
)

echo [BUILD] PyInstaller onedir noconsole ...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --noconsole ^
  --name %APPNAME% ^
  --icon "%~dp0%APPNAME%-taskbar.ico" ^
  --distpath build\dist_tmp ^
  --workpath build\work ^
  --specpath build ^
  src\main.py ^
  --add-data "%~dp0%APPNAME%.ico;." ^
  --add-data "%~dp0%APPNAME%-taskbar.ico;." ^
  --add-data "%~dp0resources;resources" ^
  --add-data "%~dp0locales;locales" ^
  --collect-all psutil ^
  --collect-all tkinter ^
  --hidden-import pystray ^
  --hidden-import PIL.ImageDraw
if errorlevel 1 (
  echo [ERROR] PyInstaller failed.
  if not defined NOPAUSE pause
  exit /b 1
)

echo [PACK] assembling %RELEASE_DIR% ...
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
robocopy "build\dist_tmp\%APPNAME%" "%RELEASE_DIR%" /E /R:1 /W:1 /NFL /NDL /NP >nul
if errorlevel 8 (
  echo [ERROR] package copy failed.
  if not defined NOPAUSE pause
  exit /b 1
)
rmdir /s /q "build\dist_tmp" 2>nul

copy /y config.json "%RELEASE_DIR%\config.json" >nul 2>nul
if exist README.md copy /y README.md "%RELEASE_DIR%" >nul
if exist README.zh-CN.md copy /y README.zh-CN.md "%RELEASE_DIR%" >nul

rem Deliverable checks: exe + runtime - guards against a silently empty package.
if not exist "%FROZEN_EXE%" (
  echo [ERROR] %APPNAME%.exe missing from the release.
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%RELEASE_DIR%\_internal\base_library.zip" (
  echo [ERROR] _internal has no runtime - the package is incomplete.
  if not defined NOPAUSE pause
  exit /b 1
)
rem Tk runtime D1: the quit/confirm dialogs need it.
rem Without these the package stays silent until the FIRST dialog - i.e. until the user
rem clicks Quit. Evidence: opencodex-helper 1.2.2 log 2026-09-19 17:00:51, init.tcl not found.
rem exe + base_library alone keep every gate green, which is how this gap survived.
if not exist "%RELEASE_DIR%\_internal\_tkinter.pyd" (
  echo [ERROR] Tk runtime missing: _internal\_tkinter.pyd - the extension module itself.
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%RELEASE_DIR%\_internal\tcl86t.dll" (
  echo [ERROR] Tk runtime missing: _internal\tcl86t.dll - the DLL that _tkinter links against.
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%RELEASE_DIR%\_internal\_tcl_data" (
  echo [ERROR] Tk runtime missing: _internal\_tcl_data - init.tcl lives in here - the first dialog would die.
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%RELEASE_DIR%\_internal\%APPNAME%-taskbar.ico" (
  echo [ERROR] taskbar icon asset missing from the release.
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%RELEASE_DIR%\config.json" (
  echo [ERROR] factory config.json missing from the release.
  if not defined NOPAUSE pause
  exit /b 1
)

set "PYTHONUTF8=1"
if defined NOSMOKE goto :smoke_done
rem Instance isolation (F11/D12): the smoke run must not read or rewrite the
rem developer's live AppData config/log - DUAL PIN (both env vars) so the config
rem file is pinned too, not just the data root. Both land in a throwaway dir
rem inside the release folder, cleaned up right after the smoke.
set "DSH_HELPER_DATA_DIR=%RELEASE_DIR%\smoke-data"
rem The smoke needs the factory config (it carries dsh_cmd), but must not rewrite
rem the shipped copy: pin it to a throwaway COPY inside smoke-data.
if not exist "%RELEASE_DIR%\smoke-data" mkdir "%RELEASE_DIR%\smoke-data"
copy /y "%RELEASE_DIR%\config.json" "%RELEASE_DIR%\smoke-data\config.json" >nul
set "DSH_HELPER_CONFIG=%RELEASE_DIR%\smoke-data\config.json"
echo [TEST] frozen smoke ...
"%FROZEN_EXE%" --smoke
set "SMOKE_RC=%errorlevel%"
set "DSH_HELPER_DATA_DIR="
set "DSH_HELPER_CONFIG="
if not "%SMOKE_RC%"=="0" (
  echo [ERROR] smoke test failed. See %RELEASE_DIR%\smoke-data\log
  if not defined NOPAUSE pause
  exit /b 1
)
type "%RELEASE_DIR%\smoke.log" 2>nul
if exist "%RELEASE_DIR%\smoke.log" del /q "%RELEASE_DIR%\smoke.log"
if exist "%RELEASE_DIR%\smoke-data" rmdir /s /q "%RELEASE_DIR%\smoke-data"
goto :smoke_next
:smoke_done
echo [SKIP] frozen smoke skipped (nosmoke): CI runners have no dsh.cmd to validate.
:smoke_next

echo.
echo [DONE] release: %FROZEN_EXE%
if defined RUN_AFTER start "" "%FROZEN_EXE%"
if not defined NOPAUSE pause
exit /b 0
