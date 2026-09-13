@echo off
rem ===== TSP backend 3018, fully hidden =====
set "PY=C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
powershell -NoProfile -Command "$env:TDX_DATA_DIR='E:\tdx\vipdoc'; Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app.main:app','--app-dir','%~dp0backend','--env-file','%~dp0.env','--host','0.0.0.0','--port','3018' -WorkingDirectory '%~dp0backend' -WindowStyle Hidden"
echo TSP backend starting hidden: http://127.0.0.1:3018
