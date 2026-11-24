@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Chay thu toan bo luong B tren 1 may bang mo phong: MongoDB + simulator + WMS ao + WCS B.
if not exist .venv call install.bat nopause
powershell -NoProfile -Command "if (-not (Test-NetConnection 127.0.0.1 -Port 27017 -InformationLevel Quiet -WarningAction SilentlyContinue)) { exit 1 }" || start "G8B MongoDB" start_mongodb.bat
timeout /t 3 /nobreak >nul
call run_simulators.bat
start "G8B WMS AO" run_wms.bat
start "G8B WCS B" run_wcs.bat
timeout /t 5 /nobreak >nul
start http://127.0.0.1:9100
start http://127.0.0.1:9000
