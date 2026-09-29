@echo off
rem ===== TSP: start BOTH backend 3018 and frontend 3011, fully hidden =====
set "PY=C:\Users\WenPaul\.local\bin\python.exe"
set "NODE=C:\Program Files\nodejs\node.exe"
set "ROOT=%~dp0"
powershell -NoProfile -Command "$env:TDX_DATA_DIR='D:\new_tdx_hd_test\vipdoc'; Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app.main:app','--app-dir','%ROOT%backend','--env-file','%ROOT%.env','--host','0.0.0.0','--port','3018' -WorkingDirectory '%ROOT%backend' -WindowStyle Hidden; Start-Process -FilePath '%NODE%' -ArgumentList '%ROOT%frontend\node_modules\vite\bin\vite.js','--host','0.0.0.0','--port','3011' -WorkingDirectory '%ROOT%frontend' -WindowStyle Hidden"
echo TSP starting hidden - frontend http://localhost:3011  backend http://127.0.0.1:3018
