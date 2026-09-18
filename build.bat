@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set PY=H:\Tools\Python\Python313\python.exe
rem Version single source of truth: VERSION in main.py (semver, see doc D15)
set VERSION=
for /f "tokens=2,*" %%a in ('findstr /b /c:"VERSION = " src\main.py') do set VERSION=%%~b
if not defined VERSION (
  echo [ERROR] Cannot read VERSION from main.py.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)
set VERSION=%VERSION:"=%
set PACKAGE=dsh-helper-%VERSION%
set EXE=%PACKAGE%.exe
if not exist "%PY%" (
  echo [ERROR] Python not found: %PY%
  if /i not "%~1"=="nopause" pause
  exit /b 1
)

echo [GATE] py_compile src/main.py + src/modules ...
"%PY%" -m py_compile src/main.py src/modules/appconfig/appconfig.py src/modules/update_helper/update_helper.py src/modules/paths/paths.py src/modules/log_kit/log_kit.py src/modules/tray_kit/tray_kit.py
if errorlevel 1 (
  echo [ERROR] compile gate failed.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)
echo [GATE] template sync check ...
"%PY%" ..\my-diy-tool-template\sync_check.py --roots dsh-helper
if errorlevel 1 (
  echo [ERROR] template drift detected. See my-diy-tool-template/sync_check.py output above.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)

rem Running-instance check: prefix match on purpose - any version of the exe
rem must block a build, not just the one this script would produce.
tasklist /fo csv 2>nul | findstr /i /c:"dsh-helper" >nul
if not errorlevel 1 (
  echo [ERROR] dsh-helper is running. Please exit the tray app first.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd-HHmmssfff')"') do set PACKAGE_ID=dsh-helper-pkg-%%i
set RELEASE_ROOT=out\%PACKAGE_ID%
set RELEASE_DIR=%RELEASE_ROOT%

if exist "build\dist_tmp" rmdir /s /q "build\dist_tmp"

echo [BUILD] PyInstaller onedir noconsole ...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --noconsole ^
  --name %PACKAGE% ^
  --distpath build\dist_tmp ^
  --workpath build\work ^
  --specpath build ^
  src/main.py ^
  --add-data "%~dp0resources;resources" ^
  --collect-all psutil ^
  --collect-all tkinter ^
  --hidden-import pystray ^
  --hidden-import PIL.ImageDraw
if errorlevel 1 (
  echo [ERROR] PyInstaller failed.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)

if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
robocopy "build\dist_tmp\%PACKAGE%" "%RELEASE_DIR%" /E /R:1 /W:1 /NFL /NDL /NP >nul
if errorlevel 8 (
  echo [ERROR] package copy failed.
  if /i not "%~1"=="nopause" pause
  exit /b 1
)

copy /y config.json "%RELEASE_DIR%\config.json" >nul 2>nul
if exist README.md copy /y README.md "%RELEASE_DIR%\README.md" >nul 2>nul
rmdir /s /q "build\dist_tmp" 2>nul

echo [TEST] smoke test ...
"%RELEASE_DIR%\%EXE%" --smoke
if errorlevel 1 (
  echo [ERROR] smoke test failed. See %RELEASE_DIR%\smoke.log
  if /i not "%~1"=="nopause" pause
  exit /b 1
)
type "%RELEASE_DIR%\smoke.log"
echo.
echo [DONE] package: %RELEASE_DIR%\%EXE%
if /i not "%~1"=="nopause" pause
exit /b 0
