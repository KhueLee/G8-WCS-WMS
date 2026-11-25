@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title G8B 2 BAN DINH DANH G8-ID (MO PHONG)
rem Chay 2 ban dinh danh G8-ID THAT o che do mo phong (MCSX001 diem san BDD01, MCSX005 diem san BDD05),
rem noi voi WMS ao + WCS B dang chay (chay run.bat demo truoc). Khong sua phan mem tram.
rem Tham so 1 (tuy chon): thu muc G8-ID-Deploy, mac dinh ..\G8-ID\G8-ID-Deploy (git clone https://github.com/KhueLee/G8-ID.git)
set PYTHONUTF8=1
set NO_PROXY=127.0.0.1,localhost
set "G8ID=%~1"
if "%G8ID%"=="" set "G8ID=%~dp0..\..\G8-ID\G8-ID-Deploy"
if not exist "%G8ID%\app\main.py" (echo [LOI] Khong thay G8-ID o %G8ID% & pause & exit /b 1)
.venv\Scripts\python -c "import flask, waitress, serial, segno" 2>nul || .venv\Scripts\python -m pip install -q -r "%G8ID%\requirements.txt"
.venv\Scripts\python tools\g8id_desks.py --g8id "%G8ID%" --desks MCSX001:BDD01,MCSX005:BDD05 --register
pause
