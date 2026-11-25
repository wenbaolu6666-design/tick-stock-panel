param([ValidateSet('gui','status','start','full-start','stop','open')][string]$Action='gui',[switch]$IncludeTdx)
$ErrorActionPreference='SilentlyContinue'
$AppRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$TspExe=Join-Path $AppRoot 'TSP.exe'
$TdxExe='D:\new_tdx_hd_test\tdxw.exe'
$BackendPort=3018
$TqPort=17709

function Get-PortPid([int]$Port){
  $line=netstat -ano | Select-String ":$Port\s+.*LISTENING" | Select-Object -First 1
  if(-not $line){return $null}
  return [int](($line.ToString().Trim() -split '\s+')[-1])
}
function Get-TspProc{ Get-Process TSP -ErrorAction SilentlyContinue | Select-Object -First 1 }
function Get-StatusText{
  $tsp=Get-TspProc
  $be=Get-PortPid $BackendPort
  $tq=Get-PortPid $TqPort
  [pscustomobject]@{
    Tsp = if($tsp){"运行中 (PID $($tsp.Id))"}else{'未运行'}
    Backend = if($be){"已监听 $BackendPort (PID $be)"}else{'未监听'}
    Tq = if($tq){"已监听 $TqPort"}else{'未启动'}
  }
}
function Start-Tsp{
  if(Get-TspProc){ Write-Host 'TSP 已在运行'; return }
  if(-not(Test-Path $TspExe)){ Write-Host "找不到 $TspExe"; return }
  Start-Process -FilePath $TspExe
  Write-Host 'TSP 启动中...'
}
function Start-Full{
  if(Get-PortPid $TqPort){
    Write-Host 'TQ 已在监听'
  } elseif(Test-Path $TdxExe){
    if(-not(Get-Process tdxw -ErrorAction SilentlyContinue)){
      Start-Process -FilePath $TdxExe -WindowStyle Normal
      $login=Join-Path $AppRoot 'autostart-tdx-login.ps1'
      if(Test-Path $login){
        Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$login`"" -WindowStyle Hidden
      }
    }
    Write-Host '等待通达信 TQ 17709 (最多180秒)...'
    for($i=0;$i -lt 90;$i++){ Start-Sleep -Seconds 2; if(Get-PortPid $TqPort){Write-Host 'TQ 已上线';break} }
  } else {
    Write-Host "未找到通达信客户端: $TdxExe (将以降级模式启动)"
  }
  Start-Tsp
}
function Stop-Tsp{
  Get-Process TSP -ErrorAction SilentlyContinue | Stop-Process -Force
  if($IncludeTdx){ Get-Process tdxw -ErrorAction SilentlyContinue | Stop-Process -Force }
  Write-Host '已停止'
}

if($Action -ne 'gui'){
  switch($Action){
    'status'{ $s=Get-StatusText; "TSP: $($s.Tsp)"; "Backend: $($s.Backend)"; "TQ: $($s.Tq)" }
    'start'{ Start-Tsp }
    'full-start'{ Start-Full }
    'stop'{ Stop-Tsp }
    'open'{ Start-Process "http://127.0.0.1:$BackendPort" }
  }
  exit
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form=New-Object System.Windows.Forms.Form
$form.Text='TSP 启动管理器'
$form.Size=New-Object System.Drawing.Size(520,320)
$form.StartPosition='CenterScreen'
$form.FormBorderStyle='FixedDialog'
$form.MaximizeBox=$false

$lblTsp=New-Object System.Windows.Forms.Label
$lblTsp.Location=New-Object System.Drawing.Point(24,20); $lblTsp.Size=New-Object System.Drawing.Size(460,22)
$lblBe=New-Object System.Windows.Forms.Label
$lblBe.Location=New-Object System.Drawing.Point(24,48); $lblBe.Size=New-Object System.Drawing.Size(460,22)
$lblTq=New-Object System.Windows.Forms.Label
$lblTq.Location=New-Object System.Drawing.Point(24,76); $lblTq.Size=New-Object System.Drawing.Size(460,22)
$form.Controls.AddRange(@($lblTsp,$lblBe,$lblTq))

$chk=New-Object System.Windows.Forms.CheckBox
$chk.Text='停止时同时关闭通达信'
$chk.Location=New-Object System.Drawing.Point(24,110); $chk.Size=New-Object System.Drawing.Size(240,24)
$form.Controls.Add($chk)

function New-Btn($text,$x,$y,$w){
  $b=New-Object System.Windows.Forms.Button
  $b.Text=$text; $b.Location=New-Object System.Drawing.Point($x,$y); $b.Size=New-Object System.Drawing.Size($w,38)
  $form.Controls.Add($b); return $b
}
$b1=New-Btn '一键启动（面板）' 24 150 220
$b2=New-Btn '全功能启动（含通达信）' 264 150 220
$b3=New-Btn '停止' 24 200 140
$b4=New-Btn '打开浏览器' 180 200 140
$b5=New-Btn '刷新状态' 336 200 148

function Refresh-Status{
  $s=Get-StatusText
  $lblTsp.Text="TSP 进程: $($s.Tsp)"
  $lblBe.Text="后端 3018: $($s.Backend)"
  $lblTq.Text="通达信 TQ 17709: $($s.Tq)"
}
$b1.Add_Click({ Start-Tsp; Start-Sleep 2; Refresh-Status })
$b2.Add_Click({ Start-Full; Start-Sleep 2; Refresh-Status })
$b3.Add_Click({ $script:IncludeTdx=$chk.Checked; Stop-Tsp; Start-Sleep 1; Refresh-Status })
$b4.Add_Click({ Start-Process "http://127.0.0.1:$BackendPort" })
$b5.Add_Click({ Refresh-Status })

$timer=New-Object System.Windows.Forms.Timer
$timer.Interval=3000
$timer.Add_Tick({ Refresh-Status })
$timer.Start()
Refresh-Status
[void]$form.ShowDialog()