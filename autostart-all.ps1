# TSP 一键启动: 后端(3018) + 前端(3011) + 通达信(TQ网关17709)

# 由任务计划程序在登录时调用; 亦可手动运行

$ErrorActionPreference = 'SilentlyContinue'

# 单实例互斥锁: 计划任务与手动运行重叠时, 后到者直接退出 (修复"重复启动")
$mutexCreated = $false
$mutex = New-Object System.Threading.Mutex($true, 'TSP-AutoStart-All', [ref]$mutexCreated)
if (-not $mutexCreated) { exit 0 }

$root = 'E:\WenPaul\Documents\量化炒股\tick-stock-panel'

$py = 'C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe'

$vite = Join-Path $root 'frontend\node_modules\.bin\vite.CMD'

$log = Join-Path $root 'logs'

New-Item -ItemType Directory -Force -Path $log | Out-Null



# 1) 通达信 (TQ 网关 17709 随登录后的量化插件上线; 已在跑则跳过)

if (-not (Get-Process tdxw -ErrorAction SilentlyContinue)) {

    Start-Process -FilePath 'E:\tdx\tdxw.exe' -WorkingDirectory 'E:\tdx'

    # 等 TdxW 进程真正出现在进程表 (避免后端 tdx_quant 探测时误判未启动而二次拉起)
    $wdx = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $wdx -and -not (Get-Process tdxw -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 500 }

}

# 1b) 登录弹窗自动处理 (游客登录/冲突对话框/风险提示)

$loginScript = Join-Path $PSScriptRoot 'autostart-tdx-login.ps1'

if (Test-Path $loginScript) {

    Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File', $loginScript -WindowStyle Hidden

}



# 2) 后端 3018 (已在监听则跳过)

$listen = netstat -ano | Select-String ':3018\s.*LISTENING'

if (-not $listen) {

    Start-Process -FilePath $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','3018' `

        -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden `

        -RedirectStandardOutput (Join-Path $log 'backend.out.log') `

        -RedirectStandardError (Join-Path $log 'backend.err.log')

}



# 3) 前端 3011 (已在监听则跳过)

$listen = netstat -ano | Select-String ':3011\s.*LISTENING'

if (-not $listen) {

    $env:BACKEND_HOST = '127.0.0.1'

    $env:BACKEND_PORT = '3018'

    Start-Process -FilePath 'C:/Program Files/nodejs/node.exe' -ArgumentList (Join-Path $root 'frontend\node_modules\vite\bin\vite.js'),'--host','0.0.0.0','--port','3011' -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden

}



# 4) 等 TQ 网关上线 (通达信登录后 17709 才监听), 最多等 5 分钟

$deadline = (Get-Date).AddMinutes(5)

while ((Get-Date) -lt $deadline) {

    $tq = netstat -ano | Select-String ':17709\s.*LISTENING'

    if ($tq) { break }

    Start-Sleep -Seconds 10

}



# 5) 后端重载能力快照 (TQ 上线后 depth5/full_minute 等 12 插槽恢复)

try {

    Invoke-RestMethod -Method POST -Uri 'http://127.0.0.1:3018/api/settings/data-sources/reload' -TimeoutSec 60 | Out-Null

} catch {}