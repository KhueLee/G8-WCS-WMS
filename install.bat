@echo off
chcp 65001 >nul
cd /d "%~dp0"
title G8B CAI DAT VA KIEM TRA MOI TRUONG
rem Cai dat lan dau va kiem tra moi truong (chay lai bao nhieu lan cung duoc).
rem Chay bang "Run as administrator" de tu mo cong firewall cho WMS / WCS / site.
echo === 1. Python
where python >nul 2>nul || (echo   [LOI] Chua cai Python 3.10+ 64-bit, khi cai tick "Add python.exe to PATH" & goto fail)
python -c "import sys,struct; sys.exit(0 if sys.version_info>=(3,10) and struct.calcsize('P')==8 else 1)" || (echo   [LOI] Can Python 3.10 tro len ban 64-bit, may dang co: & python --version & goto fail)
for /f "delims=" %%v in ('python --version') do echo   [OK] %%v

echo === 2. Moi truong .venv va thu vien
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || (echo   [LOI] Tao .venv that bai & goto fail)
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check --upgrade pip
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || (echo   [LOI] Cai thu vien that bai - kiem tra mang / proxy & goto fail)
echo   [OK] Da cai thu vien
if not exist data\db mkdir data\db
if not exist logs mkdir logs

echo === 3. Tuong lua (firewall)
net session >nul 2>nul
if errorlevel 1 (
  echo   [BO QUA] Khong chay bang quyen Administrator nen chua mo cong. Muon mo: chuot phai install.bat - Run as administrator
) else (
  .venv\Scripts\python -m g8b.ctl firewall
)

echo === 4. Kiem tra moi truong
.venv\Scripts\python -m g8b.ctl check
if errorlevel 1 goto fail
echo.
echo [OK] San sang. Chay he thong: run.bat
if /i not "%~1"=="nopause" pause
exit /b 0

:fail
echo.
echo [LOI] Chua san sang - sua cac muc [LOI] o tren roi chay lai install.bat
if /i not "%~1"=="nopause" pause
exit /b 1
