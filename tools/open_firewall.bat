@echo off
rem Mo cong WCS B (9000), WMS ao (9100) cho may khac / RCS goi vao. Chay bang Run as administrator.
netsh advfirewall firewall add rule name="G8B WCS 9000" dir=in action=allow protocol=TCP localport=9000
netsh advfirewall firewall add rule name="G8B WMS 9100" dir=in action=allow protocol=TCP localport=9100
pause
