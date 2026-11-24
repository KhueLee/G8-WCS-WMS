@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
rem Test dau cuoi (14 kich ban) tren cong rieng 17100-19701, DB rieng e2e_*. Can MongoDB o 27017.
.venv\Scripts\python tests\e2e_test.py
pause
