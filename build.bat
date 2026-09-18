@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem dsh-helper build: gates -> PyInstaller -> release\<name>-<ver>\ -> frozen smoke
rem ASCII-only: cmd.exe parses .bat with the machine ANSI code page.
rem Usage: build.bat [norun] [nopause]
rem   norun    do not start the built exe (starting it is the default)
rem   nopause  unattended (no "press any key") - used by CI
rem ---------------------------------------------------------------------------

set RUN_AFTER=1
set NOPAUSE=
set BUILD_ARGS=%*
if not defined BUILD_ARGS goto :args_done
for %%a in (%BUILD_ARGS%) do (
  if /i "%%a"=="norun" set RUN_AFTER=
  if /i "%%a"=="nopause" set NOPAUSE=1
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
rem Backslash path: cmd splits forward-slash paths inside for /f (J pitfall).
set VERSION=
for /f "tokens=2,*" %%a in ('findstr /b /c:"VERSION = " src\main.py') do set VERSION=%%~b
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

rem Running-instance check: prefix match - any version blocks a build.
tasklist /fo csv 2>nul | findstr /i /c:"%APPNAME%" >nul
if not errorlevel 1 (
  echo [ERROR] %APPNAME% is running. Exit it from the tray before building.
  if not defined NOPAUSE pause
  exit /b 1
)

echo [GATE] py_compile src\main.py + src\modules ...
"%PY%" -m py_compile src\main.py src\modules\appconfig\appconfig.py src\modules\update_helper\update_helper.py src\modules\paths\paths.py src\modules\log_kit\log_kit.py src\modules\tray_kit\tray_kit.py
if errorlevel 1 (
  echo [ERROR] compile gate failed.
  if not defined NOPAUSE pause
  exit /b 1
)

echo [GATE] template sync check ...
"%PY%" ..\my-diy-tool-template\sync_check.py --roots dsh-helper
if errorlevel 1 (
  echo [ERROR] template drift detected. See my-diy-tool-template/sync_check.py output above.
  if not defined NOPAUSE pause
  exit /b 1
)

echo [BUILD] PyInstaller onedir noconsole ...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --noconsole ^
  --name %APPNAME% ^
  --distpath build\dist_tmp ^
  --workpath build\work ^
  --specpath build ^
  src\main.py ^
  --add-data "%~dp0resources;resources" ^
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

set "PYTHONUTF8=1"
echo [TEST] frozen smoke ...
"%FROZEN_EXE%" --smoke
if errorlevel 1 (
  echo [ERROR] smoke test failed. See %RELEASE_DIR%\log
  if not defined NOPAUSE pause
  exit /b 1
)
type "%RELEASE_DIR%\smoke.log" 2>nul
if exist "%RELEASE_DIR%\smoke.log" del /q "%RELEASE_DIR%\smoke.log"
if exist "%RELEASE_DIR%\log" rmdir /s /q "%RELEASE_DIR%\log"

echo.
echo [DONE] release: %FROZEN_EXE%
if defined RUN_AFTER start "" "%FROZEN_EXE%"
if not defined NOPAUSE pause
exit /b 0
