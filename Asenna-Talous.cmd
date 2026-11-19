@echo off
rem Huoltokello-Talous - asennuksen kaynnistin
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Asenna-Talous.ps1" -Automaattikaynnistys -Ajastettu %*
pause
