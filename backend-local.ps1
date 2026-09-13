$env:PYTHONUNBUFFERED = '1'
$envFile = Join-Path $PSScriptRoot '.env'
# 规则：不使用项目内 .venv，统一用系统锁定的 Python 3.12（工具统一在系统侧）
$py = 'C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe'
& $py -m uvicorn app.main:app --app-dir (Join-Path $PSScriptRoot 'backend') --env-file $envFile --host 0.0.0.0 --port 3018
