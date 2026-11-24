@echo off
chcp 65001 >nul
cd /d "%~dp0"
title G8B MongoDB 27018 (KHONG DONG CUA SO NAY)
rem MongoDB rieng cua luong B o cong 27018 (khong dung chung cong 27017 voi WCS goc / G8-ID).
rem Doi cong: sua so 27018 o day va "mongo.uri" trong config\wcs.json, config\wms.json.
set PORT=27018
if not exist data\db mkdir data\db
if not exist logs mkdir logs
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [OK] Da co MongoDB nghe cong %PORT% - khong can mo them.
  timeout /t 5 >nul
  exit /b 0
)
mongodb\bin\mongod.exe --dbpath data\db --bind_ip 127.0.0.1 --port %PORT% --wiredTigerCacheSizeGB 0.5 --logpath logs\mongod.log --logappend
