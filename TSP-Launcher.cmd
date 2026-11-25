@echo off
chcp 65001 >nul
rem TSP on-demand launcher. Usage: TSP-Launcher.cmd status|start|start-all|stop|restart|open
set PS=pwsh.exe
where %PS% >nul 2>nul || set PS=powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0TSP-Manager.ps1" -Action %*
