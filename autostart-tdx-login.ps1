# 通达信登录弹窗自动处理 v5: DPI-aware + 真实控件点击(不再用固定几何坐标)
# 根因修复: 新电脑 150% 缩放时, v4 的 GetWindowRect 返回逻辑坐标而 SetCursorPos 用物理像素, 点击全部偏移。
# v5: SetProcessDPIAware() 后坐标一致; 弹窗/页签/按钮全部 EnumChildWindows 找真实控件, BM_CLICK 或中心点击。
# 流程: 风险揭示#32770(确定) → 登录窗(#32770 标题'通达信金融终端*') 点游客页签 → TCM_GETCURSEL=1 校验 → BM_CLICK 登录
#       → 错误弹窗(请输入用户名和密码)自动关掉重试一次 → 通达信信息 CEF 公告 WM_CLOSE → 等 17709
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
  public const uint BM_CLICK = 0x00F5, WM_CLOSE = 0x0010;
  public const uint TCM_GETCURSEL = 0x130B, TCM_GETITEMCOUNT = 0x1304;
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

$log = 'E:\WenPaul\Documents\量化炒股\tick-stock-panel\logs\tdx-login-auto.log'
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

function Get-TdxWindows {
    # 返回 tdxw 进程的可见顶层窗口: hwnd/class/title/rect
    $list = @()
    $cb = {
        param($h, $l)
        if (-not [TdxAuto]::IsWindowVisible($h)) { return $true }
        $p = 0
        [TdxAuto]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
        $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
        if (-not $proc -or $proc.ProcessName -notmatch 'tdxw') { return $true }
        $sb = New-Object System.Text.StringBuilder 256
        [TdxAuto]::GetWindowTextW($h, $sb, 256) | Out-Null
        $cn = New-Object System.Text.StringBuilder 128
        [TdxAuto]::GetClassNameW($h, $cn, 128) | Out-Null
        $r = New-Object TdxAuto+RECT
        [TdxAuto]::GetWindowRect($h, [ref]$r) | Out-Null
        $script:list += ,@{ hwnd=$h; class=$cn.ToString(); title=$sb.ToString(); x=$r.Left; y=$r.Top; w=($r.Right-$r.Left); h=($r.Bottom-$r.Top) }
        return $true
    }
    [TdxAuto]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    return $list
}

function Get-ChildControls([IntPtr]$parent) {
    $list = @()
    $cb = {
        param($h, $l)
        if (-not [TdxAuto]::IsWindowVisible($h)) { return $true }
        $sb = New-Object System.Text.StringBuilder 256
        [TdxAuto]::GetWindowTextW($h, $sb, 256) | Out-Null
        $cn = New-Object System.Text.StringBuilder 128
        [TdxAuto]::GetClassNameW($h, $cn, 128) | Out-Null
        $r = New-Object TdxAuto+RECT
        [TdxAuto]::GetWindowRect($h, [ref]$r) | Out-Null
        $script:list += ,@{ hwnd=$h; class=$cn.ToString(); text=$sb.ToString(); x=$r.Left; y=$r.Top; w=($r.Right-$r.Left); h=($r.Bottom-$r.Top) }
        return $true
    }
    [TdxAuto]::EnumChildWindows($parent, $cb, [IntPtr]::Zero) | Out-Null
    return $list
}

function TqUp { return [bool](netstat -ano | Select-String ':17709\s.*LISTENING') }

$deadline = (Get-Date).AddMinutes(8)
$tabClicks = 0
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process tdxw -ErrorAction SilentlyContinue)) { Start-Sleep -Seconds 3; continue }
    if (TqUp) { Log "TQ 17709 上线, 退出"; break }

    $wins = Get-TdxWindows

    # 1) 风险揭示等标准对话框: #32770 带'确定'按钮 → BM_CLICK
    foreach ($w in $wins) {
        if ($w.class -eq '#32770' -and $w.title -notlike '通达信金融终端*') {
            $kids = Get-ChildControls $w.hwnd
            $ok = $kids | Where-Object { $_.class -eq 'Button' -and $_.text -match '确定|确认|关闭' } | Select-Object -First 1
            if ($ok) {
                [void][TdxAuto]::PostMessage($ok.hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
                Log ("dialog dismissed: '{0}' btn={1}" -f $w.title, $ok.text)
                Start-Sleep -Seconds 2
            }
        }
    }

    # 2) 登录窗: #32770 + 标题'通达信金融终端*' + SysTabControl32 子控件
    $login = $wins | Where-Object { $_.class -eq '#32770' -and $_.title -like '通达信金融终端*' } | Select-Object -First 1
    if ($login) {
        $kids = Get-ChildControls $login.hwnd
        $tab = $kids | Where-Object { $_.class -eq 'SysTabControl32' } | Select-Object -First 1
        $btn = $kids | Where-Object { $_.class -eq 'Button' -and $_.text -eq '登录' } | Select-Object -First 1
        if ($tab -and $btn) {
            $sel = [int][TdxAuto]::SendMessage($tab.hwnd, 0x130B, [IntPtr]::Zero, [IntPtr]::Zero)
            if ($sel -ne 1) {
                # 真实点击游客页签(第二个): 页签条右 25% 处
                if ($tabClicks -lt 3) {
                    [void][TdxAuto]::SetForegroundWindow($login.hwnd)
                    Start-Sleep -Milliseconds 300
                    $cx = [int]($tab.x + $tab.w * 0.75)
                    $cy = [int]($tab.y + $tab.h / 2)
                    [TdxAuto]::Click($cx, $cy)
                    $tabClicks++
                    Log ("guest tab click #${tabClicks} at (${cx},{cy})")
                    Start-Sleep -Seconds 2
                }
            } else {
                # 已在游客页 → BM_CLICK 登录
                [void][TdxAuto]::PostMessage($btn.hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
                Log "login BM_CLICK (guest)"
                Start-Sleep -Seconds 4
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
Log "=== 结束 ==="
