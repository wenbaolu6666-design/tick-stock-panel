@echo off
rem ===== Stop TSP backend by port 3018 =====
set "PORT=3018"
set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  taskkill /F /PID %%P >nul 2>&1
  set "FOUND=1"
  echo Stopped TSP backend on port %PORT%, PID %%P
)
if "%FOUND%"=="0" echo Port %PORT% is not running
