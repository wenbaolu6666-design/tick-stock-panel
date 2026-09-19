# TSP 系统状态备忘(tsp-system-state)

> 维护约定:每次改动自启动链/数据源栈后更新本文件。
> 最后更新:2026-09-19(v7,修复通达信启动链)

## 通达信自启动链(当前 v7 + autostart-all v3 全静默)

### 启动链(2026-09-19 伪重启实测 60 秒全栈恢复)
- 计划任务 `TSP-AutoStart`(AtLogOn + SessionUnlock, principal=PAUL\WenPaul, Interactive)
  → Action: `wscript.exe autostart-silent.vbs`(UTF-16 编码, 隐藏窗口零闪现)
  → `powershell autostart-all.ps1`(v3, 互斥锁 `TSP-AutoStart-All`, 打点日志 `logs/autostart-all.log`)
  1. 启动 `E:	dx	dxw.exe`(已跑则跳过)
  2. 静默拉起 `autostart-tdx-login.ps1`(v7, CreateNoWindow)
  3. **等 TQ 17709 上线(最多 5 分钟)—— 必须在 backend 之前**: backend 启动时 daily_pipeline
     立即用 tdx_quant provider 同步 instruments, TQ 未上 → ValueError → backend 启动失败
  4. 后端 3018(cmd /c CreateNoWindow + 日志重定向; 40s 未监听自动重试一次 — 兜 duckdb 启动竞态)
  5. 前端 3011(Start-Process node -WindowStyle Hidden; **cmd /c 间接启动 node 实测失败**)
  6. POST `/api/settings/data-sources/reload`
- v7 冲突框语义(按官方弹窗文本): 是(Y)=接管杀旧实例 / **否(N)=两实例并存(冲突, 绝不点)** / 取消=结束本次运行
  → 已有主窗口(mainExists=True)时点「取消」; 无主窗口(残留锁)点「是」放行
- v7 点击上限: tab 12 次 / login 12 次

### 排障备忘(2026-09-19 伪重启会话踩坑)
- 计划任务注册脚本含中文路径必须 UTF-8 BOM, 否则任务 XML 路径乱码(閲忓寲鐐掕偂)→ LastResult=0xFFFD0000 脚本不执行
- Python heredoc 写 PowerShell 路径: `
`/``/`` 会被 Python 转义成控制字符写进 ps1(用 chr(92) 拼)
- 任务实例挂起(如 powershell -File 报错等待输入)会持互斥锁 → 后续触发被 IgnoreNew 静默忽略
  → 先 `Stop-ScheduledTask` + 杀 powershell/wscript 实例再触发

### 端到端实测(2026-09-19 18:37)

### 启动链
- 计划任务 `TSP-AutoStart`(AtLogOn + SessionUnlock)→ `autostart-all.ps1`(互斥锁 `TSP-AutoStart-All` 防重叠)
  1. 启动 `E:\tdx\tdxw.exe`(已跑则跳过),等进程出现(最多 15s)
  2. 拉起 `autostart-tdx-login.ps1`(Hidden,互斥锁 `TSP-TdxLogin-Auto`)
  3. 后端 3018(uvicorn,已在监听则跳过)
  4. 前端 3011(node vite,已在监听则跳过)
  5. 等 TQ 17709 上线(最多 5 分钟)→ POST `/api/settings/data-sources/reload` 重载能力快照
- `autostart-tdx-login.ps1`(v7)职责:关风险揭示弹窗 → 登录窗切游客页签 → BM_CLICK 登录 → 关错误弹窗/公告窗 → 等 17709
- 日志:`logs/tdx-login-auto.log`(主循环带 60s 限频 diag 日志)

### v7 关键根因(2026-09-19 实测,勿回退)
1. **风险揭示弹窗**:`E:\tdx` 的通达信是「内部测试版本」,每次启动弹模态框(空标题 `#32770`,
   Static 文本含"风险揭示"),阻塞主程序初始化(进程 ~26MB、无任何窗口、不写 `T0001\tdxsys3.log`),
   挂几分钟后 tdxw 自动退出(日志"开始退出TP等模块")。
   **该弹窗对 BM_CLICK / Enter / WM_COMMAND / 真鼠标点击全部免疫,仅 `WM_CLOSE (0x0010)` 有效** —
   v7 在 BM_CLICK 后 2 秒复核 `IsWindowVisible`,弹窗仍在则对父窗口发 WM_CLOSE。
2. **PowerShell 5.1 EnumWindows 回调作用域坑**:`$script:list += ...` 在 native 回调里静默丢失
   (函数内局部变量用 `$script:` 前缀在回调里解析到的是 script scope,那里没有该变量);
   回调里调 `Get-Process` 等 cmdlet 也不可靠。
   正确写法:**函数内用 `$script:xxx = New-Object System.Collections.ArrayList` 声明,回调内 `[void]$script:xxx.Add(...)`,
   进程过滤用回调外预取的 pid 集合(`$script:tdxPids -notcontains $p`)**。
3. 诊断日志必须有限频输出,否则"零日志"无法区分「进程不在」还是「枚举失败」。

### 端到端实测(2026-09-19 18:37)
```
18:36:57 dialog '' → 点击[确定] (mainExists=False)   ← 风险揭示弹窗(BM_CLICK 无效→WM_CLOSE)
18:37:04 guest tab click #1
18:37:10 login BM_CLICK #1
18:37:24 info popup closed
18:37:40 TQ 17709 上线, 退出
```

## 服务栈健康基线
- 前端 3011(HTTP 200)/ 后端 3018 / TQ 17709(由 tdxw 量化插件监听)
- 能力矩阵:`GET /api/settings/capability-matrix`(32 插槽);TQ 上线后
  `daily/minute/full_minute/depth5` 的 `tdx_quant` 候选应为 OK(depth5/full_minute effective=tdx_quant)
- 日K接口:`GET /api/kline/daily?symbol=600519.SH&days=10`(**symbol 必须带 .SH/.SZ 后缀;days ≥ 10**)
- `/api/health`、`/api/healthz` 均 404(无此路由)

## 通达信环境
- 量化版客户端:`E:\tdx\tdxw.exe`(V7.80 内部测试版,9/11 安装;自带 TQ 插件监听 17709)
- 文档 `local-data-source-plugins.md` 中 `D:\new_tdx_mock` 已过时,现路径为 `E:\tdx`
- 已知风险:测试版弹「风险揭示」且存在启动后自动退出的现象;若测试版彻底失效需换正式版+重装 TQ 插件

## 环境备忘(勿踩)
- Python 强制系统版全路径:`C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe`
  (裸 `python` 是商店 alias 会静默失败)
- PowerShell 中文脚本必须 **UTF-8 WITH BOM + CRLF**(Write 工具不写 BOM,需系统 Python 补)
- git:`origin` 只读上游;推送用 `personal`(代理 `http.proxy=http://127.0.0.1:7897`)
- 通达信「盘后数据下载」需勾选 1/5 分钟线;464 只次新股分钟回补脚本 `tools_minute_sina_backfill.py` 可重跑补漏
- Tab 消息号:TCM_GETITEMW=0x133C、TCM_SETCURSEL=0x130C、TCM_GETCURSEL=0x130B(v7 脚本内已用)
