$env:BACKEND_HOST = '127.0.0.1'
$env:BACKEND_PORT = '3018'
Set-Location (Join-Path $PSScriptRoot 'frontend')
& .\node_modules\.bin\vite.CMD --host 0.0.0.0 --port 3011
