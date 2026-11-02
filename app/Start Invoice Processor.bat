@echo off
REM Double-click this file to start the Invoice Processor.
REM The first time, it installs what it needs (1-3 minutes, needs internet). After that it starts straight away.
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto start

echo.
echo  First-time setup: installing components. This only happens once...
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

%PY% -m venv .venv
if errorlevel 1 goto nopython
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto installfail
echo.
echo  Setup complete.

:start
start "" ".venv\Scripts\pythonw.exe" "Invoice Processor.pyw"
exit /b 0

:nopython
echo.
echo  Python was not found. Please install Python from https://www.python.org/downloads/
echo  and TICK "Add python.exe to PATH" during installation. Then double-click this file again.
echo  (See the Installation Guide, step 1.)
echo.
pause
exit /b 1

:installfail
echo.
echo  Installing the components failed. Check your internet connection and try again.
echo  If your company uses a proxy or firewall, ask IT to allow access to pypi.org.
rmdir /s /q .venv
pause
exit /b 1
