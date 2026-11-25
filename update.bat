@echo off
chcp 65001 >nul
rem chay tu ban chep trong %TEMP%: git co the thay chinh file nay khi dang chay
if /i not "%~2"=="__copy" (
  copy /y "%~f0" "%TEMP%\g8b_update.bat" >nul
  "%TEMP%\g8b_update.bat" "%~1" __copy "%~dp0."
)
cd /d "%~3"
title G8B CAP NHAT HE THONG
rem Cap nhat G8 luong B len ban moi nhat tren GitHub:
rem   1. sao luu config\ vao backup\config_<thoi gian>
rem   2. dung he thong neu dang chay
rem   3. lay code moi (git), giu nguyen cau hinh cua may nay
rem   4. cai dat + kiem tra moi truong (install.bat)
rem   5. chay lai he thong neu truoc do dang chay
rem Du lieu MongoDB (data\db) va logs\ khong bi dong toi.

where git >nul 2>nul || (echo [LOI] May nay chua cai Git: https://git-scm.com/download/win & pause & exit /b 1)
if not exist .git (echo [LOI] Thu muc nay khong phai ban clone tu GitHub, khong cap nhat tu dong duoc. & pause & exit /b 1)

for /f %%i in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%i
for /f %%i in ('git rev-parse --short HEAD') do set OLD=%%i
echo === Kiem tra ban moi tren nhanh %BRANCH% ...
git fetch origin %BRANCH% || (echo [LOI] Khong ket noi duoc GitHub. & pause & exit /b 1)
for /f %%i in ('git rev-parse --short origin/%BRANCH%') do set NEW=%%i
if "%OLD%"=="%NEW%" (
  echo [OK] Dang o ban moi nhat ^(%OLD%^), khong can cap nhat.
  if not "%~1"=="nopause" pause
  exit /b 0
)
echo Cac thay doi se cap nhat:
git log --oneline HEAD..origin/%BRANCH%
echo.
if not "%~1"=="nopause" (
  choice /m "Cap nhat tu %OLD% len %NEW%"
  if errorlevel 2 exit /b 0
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set BK=backup\config_%TS%
echo === Sao luu cau hinh vao %BK%
xcopy config "%BK%\" /e /i /q /y >nul || (echo [LOI] Sao luu that bai, dung cap nhat. & pause & exit /b 1)

set WAS=0
if exist run.bat if exist g8b\ctl.py if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -m g8b.ctl running && set WAS=1
)
if "%WAS%"=="1" (
  echo === Dung he thong dang chay
  call "%CD%\run.bat" stop
)

echo === Lay code moi
git reset --hard origin/%BRANCH% >nul || (echo [LOI] Lay code that bai. Cau hinh cu o %BK% & pause & exit /b 1)
rem tra lai cau hinh cua may nay (muc cau hinh moi tu lay gia tri mac dinh)
xcopy "%BK%" config\ /e /i /q /y >nul

echo === Cai dat va kiem tra moi truong
call "%CD%\install.bat" nopause
if errorlevel 1 (echo [LOI] Moi truong chua dat - xem cac muc [LOI] o tren. & pause & exit /b 1)

if "%WAS%"=="1" (
  echo === Chay lai he thong
  start "G8B CHAY HE THONG" "%CD%\run.bat"
)
echo.
echo [OK] Da cap nhat %OLD% -^> %NEW%. Cau hinh cu sao luu o %BK%
if "%WAS%"=="0" echo      He thong chua chay: mo run.bat de chay.
if not "%~1"=="nopause" pause
