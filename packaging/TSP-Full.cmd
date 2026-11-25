@echo off
chcp 65001 >nul
set PS=pwsh.exe
where %PS% >nul 2>nul || set PS=powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0TSP-AppManager.ps1" -Action full-start
