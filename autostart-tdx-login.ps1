# 通达信登录弹窗自动处理 v4: UIA 点对话框按钮 + Win32 枚举窗口几何点击登录页签/登录钮
# 必须保存为 UTF-8 WITH BOM。日志: logs/tdx-login-auto.log
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Win32Click {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public const uint LEFTDOWN = 0x02; public const uint LEFTUP = 0x04;
    public static void Click(int x, int y) {
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(60);
        mouse_event(LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(40);
        mouse_event(LEFTUP, 0, 0, 0, UIntPtr.Zero);
    }
}
"@

$log = 'E:\WenPaul\Documents\量化炒股\tick-stock-panel\logs\tdx-login-auto.log'
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

# 找标题以 prefix 开头、可见、有尺寸的通达信窗口, 返回句柄+矩形
function Find-TdxWindow([string]$prefix) {
    $result = $null
    $cb = {
        param($hWnd, $lParam)
        if (-not [Win32Click]::IsWindowVisible($hWnd)) { return $true }
        $sb = New-Object System.Text.StringBuilder 256
        [Win32Click]::GetWindowText($hWnd, $sb, 256) | Out-Null
        $t = $sb.ToString()
        if ($t.StartsWith($prefix)) {
            $r = New-Object Win32Click+RECT
            [Win32Click]::GetWindowRect($hWnd, [ref]$r) | Out-Null
            if ($r.Right - $r.Left -gt 100 -and $r.Bottom - $r.Top -gt 100) {
                $script:result = @{ hwnd = $hWnd; title = $t; x = $r.Left; y = $r.Top; w = ($r.Right - $r.Left); h = ($r.Bottom - $r.Top) }
                return $false
            }
        }
        return $true
    }
    [Win32Click]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    return $script:result
}

$deadline = (Get-Date).AddMinutes(8)
$tabClicked = $false
$loginClicked = $false

while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process tdxw -ErrorAction SilentlyContinue)) { Start-Sleep -Seconds 3; continue }

    # 1) 登录窗 (Win32 枚举, 标题前缀匹配)
    $loginWin = Find-TdxWindow '通达信金融终端'
    if ($loginWin -ne $null) {
        Log ("login win: hwnd={0} '{1}' rect=({2},{3}) {4}x{5}" -f $loginWin.hwnd, $loginWin.title, $loginWin.x, $loginWin.y, $loginWin.w, $loginWin.h)
        if (-not $tabClicked) {
            # 游客精选行情登录页签: 窗口内相对 (596, 346)
            [Win32Click]::Click([int]($loginWin.x + 596), [int]($loginWin.y + 346))
            $tabClicked = $true
            Log "tab clicked (guest)"
            Start-Sleep -Milliseconds 900
        }
        if (-not $loginClicked) {
            # 登录按钮: 窗口内相对 (788, 390)
            [Win32Click]::Click([int]($loginWin.x + 788), [int]($loginWin.y + 390))
            $loginClicked = $true
            Log "login clicked"
            Start-Sleep -Seconds 5
        }
    }

    # 2) TQ 上线即退出
    $tq = netstat -ano | Select-String ':17709\s.*LISTENING'
    if ($tq) { Log "TQ 17709 上线, 退出"; break }

    Start-Sleep -Seconds 6
}
Log "=== 结束 ==="
