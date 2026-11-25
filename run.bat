@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Chay G8 luong B: MongoDB rieng + WMS ao + WCS B + site giao dien.
rem   run.bat          chay that (tro toi RCS / tram theo config)
rem   run.bat demo     chay them robot / ban dinh danh / bang tai gia de thu tren 1 may
rem   run.bat stop     dung toan bo
rem   run.bat status   xem trang thai
rem Moi thanh phan chay trong 1 cua so thu nho "G8B ..." va tu chay lai neu bi dung.
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set NO_PROXY=127.0.0.1,localhost
set "PY=%~dp0.venv\Scripts\python.exe"
if /i "%~1"=="__svc" goto svc
if /i "%~1"=="__mongo" goto mongo
if not exist "%PY%" (echo [LOI] Chua cai dat: chay install.bat truoc. & pause & exit /b 1)
for /f "tokens=1-4" %%a in ('call "%PY%" -m g8b.ctl ports') do (set MP=%%a& set WMP=%%b& set WCP=%%c& set WEP=%%d)
if /i "%~1"=="stop" goto stop
if /i "%~1"=="status" goto status

title G8B CHAY HE THONG
if not exist data\db mkdir data\db
if not exist logs mkdir logs
echo === Khoi dong G8 luong B
call :up %MP% "G8B MongoDB %MP%" __mongo %MP%
call :wait %MP% 30
if /i "%~1"=="demo" call :sims
call :up %WMP% "G8B WMS AO %WMP%" __svc wms
call :up %WCP% "G8B WCS B %WCP%" __svc wcs
call :up %WEP% "G8B SITE %WEP%" __svc web
call :wait %WEP% 30
call :wait %WMP% 30
call :wait %WCP% 30
echo.
echo === Trang thai
"%PY%" -m g8b.ctl status
start "" http://127.0.0.1:%WEP%
echo.
echo Cac cua so "G8B ..." thu nho tren thanh tac vu la chuong trinh dang chay - KHONG dong.
echo Dung toan bo: run.bat stop     Xem trang thai: run.bat status
echo Cua so nay tu dong sau 20 giay.
timeout /t 20 >nul
exit /b 0

:up
"%PY%" -m g8b.ctl up %1
if not errorlevel 1 (echo   [OK] %~2 da chay san& exit /b 0)
echo   Bat %~2
start "%~2" /min "%~f0" %3 %4
exit /b 0

:wait
for /l %%i in (1,1,%2) do (
  "%PY%" -m g8b.ctl up %1 && exit /b 0
  timeout /t 1 /nobreak >nul
)
echo   [CANH BAO] Cong %1 chua mo sau %2 giay - xem cua so "G8B ..." va thu muc logs
exit /b 0

:sims
rem Mo phong: RCS gia 7100, ban dinh danh gia MCSX009 9501, bang tai xuat gia BCXB001 9701
"%PY%" -m g8b.ctl up 7100 || start "G8B SIM RCS" /min "%PY%" -m g8b.sim rcs --port 7100 --callback http://127.0.0.1:%WCP%/rcs/taskStatusReport --points config\locations.xlsx --extra-points BDD01,BDD02,BDD03,BDD04,BDD05,BDD06,BDD07,BDD08,BDD09,BDD10,BDD11,BDD12,BCXB01,BCXB02,BCXB03
"%PY%" -m g8b.ctl up 9501 || start "G8B SIM IDS MCSX009" /min "%PY%" -m g8b.sim ids --port 9501 --line MCSX009 --done-url http://127.0.0.1:%WCP%/identify-service/ids/pallet_identify_done
"%PY%" -m g8b.ctl up 9701 || start "G8B SIM SCS BCXB001" /min "%PY%" -m g8b.sim scs --port 9701 --line BCXB001 --report-url http://127.0.0.1:%WCP%/export-service/scs/taskStatusReport
echo   [OK] Mo phong: RCS http://127.0.0.1:7100  Ban dinh danh http://127.0.0.1:9501  Bang tai http://127.0.0.1:9701
exit /b 0

:stop
echo === Dung G8 luong B
"%PY%" -m g8b.ctl stop
exit /b 0

:status
"%PY%" -m g8b.ctl status
if /i not "%~2"=="nopause" pause
exit /b 0

:svc
title G8B %~2 (KHONG DONG CUA SO NAY)
:svcloop
"%PY%" -m g8b.%~2
echo [%date% %time%] g8b.%~2 dung (ma %errorlevel%), tu chay lai sau 3 giay... Xem thu muc logs
timeout /t 3 /nobreak >nul
goto svcloop

:mongo
title G8B MongoDB %~2 (KHONG DONG CUA SO NAY)
:mongoloop
rem cache du lieu toi da 1 GB RAM
"%~dp0mongodb\bin\mongod.exe" --dbpath data\db --bind_ip 127.0.0.1 --port %~2 --wiredTigerCacheSizeGB 1 --logpath logs\mongod.log --logappend
echo [%date% %time%] MongoDB dung (ma %errorlevel%), tu chay lai sau 5 giay... Xem logs\mongod.log
timeout /t 5 /nobreak >nul
goto mongoloop
