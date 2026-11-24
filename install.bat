@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Cai dat G8 luong B (WMS ao + WCS B) ===
where python >nul 2>nul || (echo [LOI] Chua cai Python 3.11+ 64-bit, tick "Add python.exe to PATH" & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install -r requirements.txt || (echo [LOI] Cai thu vien that bai & pause & exit /b 1)
if not exist data\db mkdir data\db
if not exist logs mkdir logs
echo [OK] Xong. Mo cong firewall neu can: chay tools\open_firewall.bat bang Run as administrator
if not "%1"=="nopause" pause
