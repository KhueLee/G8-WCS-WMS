@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Moi truong mo phong tren 1 may: RCS gia (7100), ban dinh danh gia MCSX009 (9501), bang tai xuat gia BCXB001 (9701).
rem Chi dung khi test. Khi chay that, WCS B tro config\wcs.json sang RCS / tram that.
set PYTHONUTF8=1
start "G8B SIM RCS" .venv\Scripts\python -m g8b.sim rcs --port 7100 --callback http://127.0.0.1:9000/rcs/taskStatusReport --points config\locations.xlsx --extra-points BDD09,BCXB01
start "G8B SIM IDS MCSX009" .venv\Scripts\python -m g8b.sim ids --port 9501 --line MCSX009 --done-url http://127.0.0.1:9000/identify-service/ids/pallet_identify_done
start "G8B SIM SCS BCXB001" .venv\Scripts\python -m g8b.sim scs --port 9701 --line BCXB001 --report-url http://127.0.0.1:9000/export-service/scs/taskStatusReport
echo Da mo 3 cua so mo phong. Giao dien: RCS http://127.0.0.1:7100  IDS http://127.0.0.1:9501  SCS http://127.0.0.1:9701
