# TSP 一键启动 v3(全静默): 后端(3018) + 前端(3011) + 通达信(TQ网关17709)

# 由计划任务 TSP-AutoStart(AtLogOn, wscript 包装)调用; 亦可手动运行
# v3: 全部子进程 CreateNoWindow 启动(零闪窗), stdout/stderr 经 cmd 追加重定向到日志文件

$ErrorActionPreference = 'SilentlyContinue'

# 单实例互斥锁: 计划任务与手动运行重叠时, 后到者直接退出 (修复"重复启动")
$mutexCreated = $false
$mutex = New-Object System.Threading.Mutex($true, 'TSP-AutoStart-All', [ref]$mutexCreated)
if (-not $mutexCreated) { exit 0 }

$root = 'D:\WenPaul\Documents\量化炒股\tick-stock-panel'

$allLog = Join-Path $root 'logs/autostart-all.log'
function LogAll($m) { Add-Content -Path $allLog -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }
LogAll "=== autostart-all start (pid=$PID) ==="

# 静默启动: cmd /c 承载环境变量与日志追加重定向, CreateNoWindow 全程无窗口
function Start-Silent([string]$wd, [string]$exe, [string]$appargs, [string]$outf, [string]$errf) {
    $a = '/c cd /d "{0}" && "{1}" {2}' -f $wd, $exe, $appargs
    if ($outf -and $errf) { $a = '{0} >> "{1}" 2>> "{2}"' -f $a, $outf, $errf }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = $a
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    [void][System.Diagnostics.Process]::Start($psi)
    LogAll ("silent start: {0} {1}" -f $exe, $appargs)
}

$py = 'C:\Users\WenPaul\.local\bin\python.exe'
$node = 'C:\Program Files\nodejs\node.exe'
$log = Join-Path $root 'logs'
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'

# 1) 通达信 (TQ 网关 17709 随登录后的量化插件上线; 已在跑则跳过)
LogAll ("step1 tdxw already-running={0}" -f [bool](Get-Process tdxw -ErrorAction SilentlyContinue))
if (-not (Get-Process tdxw -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath 'D:\new_tdx_hd_test\tdxw.exe' -WorkingDirectory 'D:\new_tdx_hd_test'
    LogAll "step1 tdxw Start-Process sent"
    # 等 TdxW 进程真正出现在进程表 (避免后端 tdx_quant 探测时误判未启动而二次拉起)
    $wdx = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $wdx -and -not (Get-Process tdxw -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 500 }
}

# 1b) 登录弹窗自动处理 v7 (游客登录/风险揭示/冲突框; 自身写 logs/tdx-login-auto.log)
$loginScript = Join-Path $root 'autostart-tdx-login.ps1'
if (Test-Path $loginScript) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $loginScript
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    [void][System.Diagnostics.Process]::Start($psi)
    LogAll "step1b login v7 launched (silent)"
}

# 2) 等 TQ 网关上线 (backend 启动时 daily_pipeline 需要 tdx_quant, TQ 未上会启动失败), 最多等 5 分钟
LogAll "step2 wait TQ 17709 first (max 5min)"
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    $tq = netstat -ano | Select-String ':17709\s.*LISTENING'
    if ($tq) { break }
    Start-Sleep -Seconds 10
}
LogAll ("step2 TQ up={0}" -f [bool](netstat -ano | Select-String ':17709\s.*LISTENING'))

# 3) 后端 3018 (已在监听则跳过; duckdb 竞态偶发启动崩溃 → 40s 校验 + 重试一次)
LogAll "step3 check backend 3018"
$listen = netstat -ano | Select-String ':3018\s.*LISTENING'
if (-not $listen) {
    Start-Silent $backendDir $py '-m uvicorn app.main:app --host 127.0.0.1 --port 3018' (Join-Path $log 'backend.out.log') (Join-Path $log 'backend.err.log')
    LogAll "step3 wait backend listen 3018 (max 90s)"
    $bdead = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $bdead -and -not (netstat -ano | Select-String ':3018\s.*LISTENING')) { Start-Sleep -Seconds 2 }
    if (-not (netstat -ano | Select-String ':3018\s.*LISTENING')) {
        LogAll "step3 backend NOT listening in 90s -> retry once"
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match '3018' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Silent $backendDir $py '-m uvicorn app.main:app --host 127.0.0.1 --port 3018' (Join-Path $log 'backend.out.log') (Join-Path $log 'backend.err.log')
        $bdead = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $bdead -and -not (netstat -ano | Select-String ':3018\s.*LISTENING')) { Start-Sleep -Seconds 2 }
    }
}
LogAll ("step3 backend listening={0}" -f [bool](netstat -ano | Select-String ':3018\s.*LISTENING'))

# 4) 前端 3011 (已在监听则跳过)
LogAll "step4 check frontend 3011"
$listen = netstat -ano | Select-String ':3011\s.*LISTENING'
if (-not $listen) {
    # 环境变量经 $env: 设置, 子进程(cmd→node)自动继承 (cmd set 拼接实测失败)
    $env:BACKEND_HOST = '127.0.0.1'
    $env:BACKEND_PORT = '3018'
    # node 经 cmd /c 间接启动实测失败(cmd 引号/空格解析), 用 Start-Process 直启(旧版验证有效)
    try {
        $fp = Start-Process -FilePath $node -ArgumentList (Join-Path $root 'frontend\node_modules\vite\bin\vite.js'),'--host','0.0.0.0','--port','3011' -WorkingDirectory $frontendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $log 'frontend.out.log') -RedirectStandardError (Join-Path $log 'frontend.err.log') -PassThru
        LogAll ("step4 frontend started pid={0}" -f $fp.Id)
    } catch {
        LogAll ("step4 frontend start FAILED: {0}" -f $_.Exception.Message)
    }
}

LogAll ("step4 frontend listening={0}" -f [bool](netstat -ano | Select-String ':3011\s.*LISTENING'))

# 5) 后端重载能力快照 (TQ 上线后 depth5/full_minute 等 12 插槽恢复)
LogAll "step5 capability reload begin"
try {
    Invoke-RestMethod -Method POST -Uri 'http://127.0.0.1:3018/api/settings/data-sources/reload' -TimeoutSec 60 | Out-Null
    LogAll "step5 reload OK"
} catch { LogAll ("step5 reload failed: {0}" -f $_.Exception.Message) }
LogAll "=== autostart-all end ==="
