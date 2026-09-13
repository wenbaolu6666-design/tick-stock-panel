@echo off
rem ===== TSP frontend 3011, fully hidden =====
set "NODE=C:\Program Files\nodejs\node.exe"
powershell -NoProfile -Command "Start-Process -FilePath '%NODE%' -ArgumentList '%~dp0node_modules\vite\bin\vite.js','--host','0.0.0.0','--port','3011' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
echo TSP frontend starting hidden: http://localhost:3011
