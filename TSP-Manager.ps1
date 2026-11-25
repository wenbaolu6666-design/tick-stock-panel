param([ValidateSet('status','start','start-all','stop','restart','open')][string]$Action='status',[switch]$IncludeTdx)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Py='C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe'
$Node='C:\Program Files\nodejs\node.exe'
$Tdx='D:\new_tdx_hd_test\tdxw.exe'
$Logs=Join-Path $Root 'logs'
New-Item -ItemType Directory -Path $Logs -Force | Out-Null
$env:TDX_DATA_DIR='D:\new_tdx_hd_test\vipdoc'
function LP([int]$p){$l=netstat -ano|Select-String ":$p\s+.*LISTENING"|Select-Object -First 1;if(-not $l){return $null};[int](($l.ToString().Trim()-split '\s+')[-1])}
function Status{foreach($i in @(@(3011,'frontend'),@(3018,'backend'),@(17709,'tdx-tq'))){$q=LP $i[0];if($q){$x=Get-Process -Id $q -ErrorAction SilentlyContinue;Write-Host ("[UP] {0} port={1} pid={2} private={3}MB handles={4}" -f $i[1],$i[0],$q,[math]::Round($x.PrivateMemorySize64/1MB),$x.HandleCount)}else{Write-Host ("[DOWN] {0} port={1}" -f $i[1],$i[0])}};$e=Join-Path $Logs 'backend.err.log';if(Test-Path $e){Write-Host ("backend.err.log={0}MB" -f [math]::Round((Get-Item $e).Length/1MB,2))};Write-Host ("tdxw=" + [bool](Get-Process tdxw -ErrorAction SilentlyContinue))}
function SB{if(LP 3018){Write-Host 'backend already up';return};Start-Process $Py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','3018' -WorkingDirectory (Join-Path $Root 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs 'backend.out.log') -RedirectStandardError (Join-Path $Logs 'backend.err.log');Write-Host 'backend starting'}
function SF{if(LP 3011){Write-Host 'frontend already up';return};Start-Process $Node -ArgumentList (Join-Path $Root 'frontend\node_modules\vite\bin\vite.js'),'--host','0.0.0.0','--port','3011' -WorkingDirectory (Join-Path $Root 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs 'frontend.out.log') -RedirectStandardError (Join-Path $Logs 'frontend.err.log');Write-Host 'frontend starting'}
function ST{if(Get-Process tdxw -ErrorAction SilentlyContinue){Write-Host 'tdxw already running';return};if(-not(Test-Path $Tdx)){Write-Warning 'tdxw missing';return};Start-Process $Tdx -WindowStyle Normal;Write-Host 'tdxw launched, waiting TQ 17709';for($i=0;$i -lt 90;$i++){Start-Sleep 2;if(LP 17709){Write-Host 'TQ up';return}};Write-Warning 'TQ timeout'}
function StopP([int]$p){$q=LP $p;if($q){Stop-Process -Id $q -Force -ErrorAction SilentlyContinue;Write-Host ("stopped port {0} pid {1}" -f $p,$q)}}
switch($Action){'status'{Status};'start'{SB;SF;Start-Sleep 3;Status};'start-all'{ST;SB;SF;Start-Sleep 3;Status};'stop'{StopP 3011;StopP 3018;if($IncludeTdx){Get-Process tdxw -ErrorAction SilentlyContinue|Stop-Process -Force}};'restart'{StopP 3011;StopP 3018;Start-Sleep 2;SB;SF;Start-Sleep 3;Status};'open'{Start-Process 'http://localhost:3011'}}