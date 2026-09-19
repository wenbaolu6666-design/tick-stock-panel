# 通达信登录弹窗自动处理 v7
# v6 基础上修复: 风险揭示弹窗(内部测试版)点不掉导致 tdxw 卡在初始化/自动退出
#   实测(2026-09-19): 弹窗对 BM_CLICK/Enter/WM_COMMAND/真鼠标点击均无效,
#   只有 WM_CLOSE(标题栏X) 能关闭它 — 故对话框处理增加 WM_CLOSE 兜底并复核。
#   - 对话框: BM_CLICK → 2秒后复核, 弹窗仍在则 WM_CLOSE
#   - 冲突对话框(是Y/否N): 无主窗口点"是"放行, 已有主窗口点"否"放弃第二实例
#   - 网络就绪前不点登录(避免登录失败→错误弹窗反复)
#   - 登录重试上限 8 次; Local 互斥锁防脚本多开
#   - 主循环限频诊断日志(每60秒), 告别零日志盲猜
# 流程: 关标准对话框(确定/是Y/WM_CLOSE) → 登录窗切游客页签(TCM_GETCURSEL校验) → BM_CLICK 登录
#       → 关错误弹窗 → 等17709 → 关'通达信信息'CEF公告
# 必须保存为 UTF-8 WITH BOM。日志: logs/tdx-login-auto.log
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class TdxAuto {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumWindowsProc cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public const uint BM_CLICK = 0x00F5, WM_CLOSE = 0x0010, TCM_GETCURSEL = 0x130B;
  public static void Click(int x, int y) {
    SetCursorPos(x, y);
    System.Threading.Thread.Sleep(120);
    mouse_event(0x02, 0, 0, 0, IntPtr.Zero);
    System.Threading.Thread.Sleep(60);
    mouse_event(0x04, 0, 0, 0, IntPtr.Zero);
  }
}
"@
[void][TdxAuto]::SetProcessDPIAware()

# 单实例互斥锁 (会话内)
$mutexCreated = $false
$mutex = New-Object System.Threading.Mutex($true, 'TSP-TdxLogin-Auto', [ref]$mutexCreated)
if (-not $mutexCreated) { exit 0 }

$log = 'E:\WenPaul\Documents\量化炒股\tick-stock-panel\logs\tdx-login-auto.log'
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

function Test-Net {
    try { return [bool](Test-Connection -ComputerName 'www.baidu.com' -Count 1 -Quiet -ErrorAction SilentlyContinue) }
    catch { return $false }
}

function Get-TdxWindows {
    # 注意: 回调内必须用 ArrayList.Add 原地追加; $script:list += 在 native 回调上下文中静默丢失(实测 2026-09-19)
    # 同理 Get-Process 等 cmdlet 也不可靠 → 回调外预取 tdxw pid 集合, 回调内纯 P/Invoke
    $script:tdxPids = @(Get-Process tdxw -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
    $script:winList = New-Object System.Collections.ArrayList
    $cb = {
        param($h, $l)
        if (-not [TdxAuto]::IsWindowVisible($h)) { return $true }
        $p = 0
        [TdxAuto]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
        if ($script:tdxPids -notcontains $p) { return $true }
        $sb = New-Object System.Text.StringBuilder 256
        [TdxAuto]::GetWindowTextW($h, $sb, 256) | Out-Null
        $cn = New-Object System.Text.StringBuilder 128
        [TdxAuto]::GetClassNameW($h, $cn, 128) | Out-Null
        $r = New-Object TdxAuto+RECT
        [TdxAuto]::GetWindowRect($h, [ref]$r) | Out-Null
        [void]$script:winList.Add(@{ hwnd=$h; class=$cn.ToString(); title=$sb.ToString(); x=$r.Left; y=$r.Top; w=($r.Right-$r.Left); h=($r.Bottom-$r.Top) })
        return $true
    }
    [TdxAuto]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    return $script:winList
}

function Get-ChildControls([IntPtr]$parent) {
    $script:ctlList = New-Object System.Collections.ArrayList
    $cb = {
        param($h, $l)
        if (-not [TdxAuto]::IsWindowVisible($h)) { return $true }
        $sb = New-Object System.Text.StringBuilder 256
        [TdxAuto]::GetWindowTextW($h, $sb, 256) | Out-Null
        $cn = New-Object System.Text.StringBuilder 128
        [TdxAuto]::GetClassNameW($h, $cn, 128) | Out-Null
        $r = New-Object TdxAuto+RECT
        [TdxAuto]::GetWindowRect($h, [ref]$r) | Out-Null
        [void]$script:ctlList.Add(@{ hwnd=$h; class=$cn.ToString(); text=$sb.ToString(); x=$r.Left; y=$r.Top; w=($r.Right-$r.Left); h=($r.Bottom-$r.Top) })
        return $true
    }
    [TdxAuto]::EnumChildWindows($parent, $cb, [IntPtr]::Zero) | Out-Null
    return $script:ctlList
}

function Test-MainFrame {
    # TdxW 主窗口(TdxW_MainFrame_Class)是否已存在 → 存在说明登录完成/已有实例
    $script:mainFound = New-Object System.Collections.ArrayList
    $cb = {
        param($h, $l)
        if (-not [TdxAuto]::IsWindowVisible($h)) { return $true }
        $cn = New-Object System.Text.StringBuilder 128
        [TdxAuto]::GetClassNameW($h, $cn, 128) | Out-Null
        if ($cn.ToString() -eq 'TdxW_MainFrame_Class') { [void]$script:mainFound.Add($true); return $false }
        return $true
    }
    [TdxAuto]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    return ($script:mainFound.Count -gt 0)
}

function TqUp { return [bool](netstat -ano | Select-String ':17709\s.*LISTENING') }

Log "=== v7 启动 ==="

$deadline = (Get-Date).AddMinutes(15)
$tabClicks = 0
$loginClicks = 0
$netWaitLogged = $false
$waitLogged = $false
$lastDiag = Get-Date

while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process tdxw -ErrorAction SilentlyContinue)) {
        if (-not $waitLogged) { Log "等待 tdxw 进程启动..."; $waitLogged = $true }
        Start-Sleep -Seconds 3; continue
    }
    if (TqUp) { Log "TQ 17709 上线, 退出"; break }

    $wins = Get-TdxWindows

    # 限频诊断: 每 60 秒记录一次窗口概况 (title 可能为空, 记 class+尺寸)
    if (((Get-Date) - $lastDiag).TotalSeconds -ge 60) {
        $sum = ($wins | ForEach-Object { "{0}'{1}'{2}x{3}" -f $_.class, $_.title, $_.w, $_.h }) -join ' | '
        Log ("diag wins={0} [{1}]" -f $wins.Count, $sum)
        $lastDiag = Get-Date
    }

    # 1) 标准对话框: 风险揭示(确定/WM_CLOSE) / 错误提示(确定) / 同目录冲突(是Y/否N)
    foreach ($w in $wins) {
        if ($w.class -ne '#32770') { continue }
        if ($w.title -like '通达信金融终端*') { continue }   # 登录窗单独处理
        if ($w.title -like '通达信信息*') { continue }        # CEF 公告单独处理
        $kids = Get-ChildControls $w.hwnd
        $btns = @($kids | Where-Object { $_.class -eq 'Button' -and $_.text -match '确定|确认|关闭|^是|是\(|Yes|OK|^否|否\(|No' })
        if ($btns.Count -eq 0) { continue }
        # 冲突对话框: 已有主窗口 = 真第二实例 → 点"否"放弃; 无主窗口 = 残留锁/首启 → 点"是"放行
        $mainExists = Test-MainFrame
        $pick = $null
        if ($mainExists) { $pick = $btns | Where-Object { $_.text -match '^否|否\(|^No' } | Select-Object -First 1 }
        if (-not $pick) { $pick = $btns | Where-Object { $_.text -match '确定|确认|关闭|^是|是\(|Yes|OK' } | Select-Object -First 1 }
        if ($pick) {
            [void][TdxAuto]::PostMessage($pick.hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
            Start-Sleep -Seconds 2
            # 复核: BM_CLICK 无效(如风险揭示弹窗) → WM_CLOSE 兜底
            if ([TdxAuto]::IsWindowVisible($w.hwnd)) {
                [void][TdxAuto]::PostMessage($w.hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
                Log ("dialog '{0}' → BM_CLICK[{1}] 无效, WM_CLOSE (mainExists={2})" -f $w.title, $pick.text, $mainExists)
            } else {
                Log ("dialog '{0}' → 点击[{1}] (mainExists={2})" -f $w.title, $pick.text, $mainExists)
            }
            Start-Sleep -Seconds 2
        }
    }

    # 2) 登录窗处理
    $login = $wins | Where-Object { $_.class -eq '#32770' -and $_.title -like '通达信金融终端*' } | Select-Object -First 1
    if ($login) {
        $kids = Get-ChildControls $login.hwnd
        $tab = $kids | Where-Object { $_.class -eq 'SysTabControl32' } | Select-Object -First 1
        $btn = $kids | Where-Object { $_.class -eq 'Button' -and $_.text -eq '登录' } | Select-Object -First 1
        if ($tab -and $btn) {
            $sel = [int][TdxAuto]::SendMessage($tab.hwnd, 0x130B, [IntPtr]::Zero, [IntPtr]::Zero)
            if ($sel -ne 1) {
                if ($tabClicks -lt 6) {
                    [void][TdxAuto]::SetForegroundWindow($login.hwnd)
                    Start-Sleep -Milliseconds 300
                    $cx = [int]($tab.x + $tab.w * 0.75)
                    $cy = [int]($tab.y + $tab.h / 2)
                    [TdxAuto]::Click($cx, $cy)
                    $tabClicks++
                    Log ("guest tab click #{0}" -f $tabClicks)
                    Start-Sleep -Seconds 2
                }
            } elseif ($loginClicks -lt 8) {
                # 已在游客页: 网络就绪才点登录, 否则等待 (避免登录失败→错误弹窗反复)
                if (Test-Net) {
                    [void][TdxAuto]::PostMessage($btn.hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
                    $loginClicks++
                    Log ("login BM_CLICK #{0}" -f $loginClicks)
                    Start-Sleep -Seconds 6
                } elseif (-not $netWaitLogged) {
                    Log "网络未就绪, 等待后再登录..."
                    $netWaitLogged = $true
                    Start-Sleep -Seconds 5
                } else {
                    Start-Sleep -Seconds 5
                }
            } else {
                # 重试耗尽: 每 30 秒静默检查一次, 等 TQ 或超时
                Start-Sleep -Seconds 30
            }
        }
    }

    # 3) 登录后公告窗 '通达信信息' (CEF): WM_CLOSE
    $info = $wins | Where-Object { $_.title -like '通达信信息*' } | Select-Object -First 1
    if ($info) {
        [void][TdxAuto]::PostMessage($info.hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
        Log "info popup closed"
    }

    Start-Sleep -Seconds 4
}
Log "=== 结束 (loginClicks=$loginClicks tabClicks=$tabClicks) ==="
