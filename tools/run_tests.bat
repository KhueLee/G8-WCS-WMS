@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set NO_PROXY=127.0.0.1,localhost
rem Test dau cuoi (22 kich ban) tren cong rieng 17100-19701, DB rieng e2e_*. Can MongoDB 27018 dang chay (run.bat).
.venv\Scripts\python tests\e2e_test.py
pause
