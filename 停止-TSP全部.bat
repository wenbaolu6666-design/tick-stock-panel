@echo off
rem ===== TSP project: stop BOTH backend 3018 and frontend 3011 =====
for %%P in (3018 3011) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /PID %%I >nul 2>&1
    echo Stopped port %%P, PID %%I
  )
)
