@echo off
chcp 65001 >nul
cd /d "%~dp0"
title G8B WMS AO (KHONG DONG CUA SO NAY)
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
if not exist .venv call install.bat nopause
:loop
.venv\Scripts\python -m g8b.wms
echo [%date% %time%] WMS AO dung (ma %errorlevel%), tu chay lai sau 3 giay... Xem logs$n$n.log
timeout /t 3 /nobreak >nul
goto loop
