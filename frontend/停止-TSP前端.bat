@echo off
rem ===== Stop TSP frontend by port 3011 =====
set "PORT=3011"
set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  taskkill /F /PID %%P >nul 2>&1
  set "FOUND=1"
  echo Stopped TSP frontend on port %PORT%, PID %%P
)
if "%FOUND%"=="0" echo Port %PORT% is not running
