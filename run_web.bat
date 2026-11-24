@echo off
chcp 65001 >nul
cd /d "%~dp0"
title G8B SITE GIAO DIEN 9200 (KHONG DONG CUA SO NAY)
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
if not exist .venv call install.bat nopause
rem Giao dien chung WMS ao + WCS B: http://<IP may>:9200 (dia chi 2 app sua trong config\web.json)
:loop
.venv\Scripts\python -m g8b.web
echo [%date% %time%] Site dung (ma %errorlevel%), tu chay lai sau 3 giay... Xem logs\web\web.log
timeout /t 3 /nobreak >nul
goto loop
