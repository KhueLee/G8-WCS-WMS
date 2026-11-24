@echo off
chcp 65001 >nul
cd /d "%~dp0"
title G8B MongoDB (KHONG DONG CUA SO NAY)
rem Neu may da co MongoDB chay o 27017 thi khong can file nay.
if not exist data\db mkdir data\db
if not exist logs mkdir logs
mongodb\bin\mongod.exe --dbpath data\db --bind_ip 127.0.0.1 --port 27017 --wiredTigerCacheSizeGB 0.5 --logpath logs\mongod.log --logappend
