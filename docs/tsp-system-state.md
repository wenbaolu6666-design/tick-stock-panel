# TSP 系统状态备忘(tsp-system-state)

> 维护约定:每次改动自启动链/数据源栈后更新本文件。
> 最后更新:2026-09-28(重装后迁移)
> **2026-11-25 通达信改为手动启用**: 桌面版/后台不再自动拉起 TdxW; 设置 → 数据源 顶部新增
> 「通达信 TQ」状态条与「启用通达信」按钮 (GET/POST `/api/settings/tdx/status|launch`)。
> 安装器移除「全功能启动(含通达信)」快捷方式; 启动管理器保留「手动启用通达信」按钮。
> 同日数据修复: tdx_local 股票池过滤掉指数/板块代码(880xxx 等 9,272 条), 清理 90 天内
> 日K污染行 177,193 条; 清除 11-23/24 缺失的阻断状态后看板恢复正常 (该两日数据仍待补)。

> **2026-11-25 标准安装包**: `packaging/Output/TSP-Setup-0.3.37.exe`(约137MB) 已产出并实测。
> 内含 PyInstaller onedir 桌面版(后端+前端一体) + 26 个内置插件 + 启动管理器。
> 安装后开始菜单: TSP(一键启动面板) / TSP 全功能启动(含通达信) / TSP 启动管理器。
> 安装包 `.env` 指向 `DATA_DIR=E:/tsp_data`; 全功能启动实测 80 秒内 TQ 17709 + 面板 3018 上线, 矩阵 35/35。
> 重建链路: `pnpm exec vite build` -> `python -m PyInstaller packaging/tsp.spec --noconfirm --clean`
> -> `ISCC.exe packaging/tsp.iss /DMyAppVersion=<version>`。

> **2026-11-25 运维模式变更**: 计划任务 `TSP-AutoStart` 已 **停用**(Status=Disabled), 不再开机自启。
> 改为按需启动管理器:
>   - `TSP-Launcher.cmd status|start|start-all|stop|restart|open`
>   - `TSP-Manager.ps1 -Action <同上> [-IncludeTdx]`
>   - `start` = 后端 3018 + 前端 3011(不含通达信); `start-all` = 先拉 tdxw 等 TQ 17709 再起前后端;
>     `stop -IncludeTdx` 连同通达信一起停。
> 同时修复无效重试/资源泄漏: tdx_quant 增加 5 分钟熔断(网关不可达时批量循环立即中止, 不再逐标的
> 建连重试); mootdx 失败 client 关闭 + 5 分钟探测冷却; quote_service 定版失败指数退避(上限 10 分钟)。

> **2026-09-28 重装后迁移**:Windows 重装,项目根 E:\WenPaul\Documents\量化炒股 → D:\WenPaul\Documents\量化炒股;通达信数据目录 E:\tdx → D:\new_tdx_hd_test(V7.80 快照,vipdoc 数据新鲜至 09-25);代理 7897 → Clash Verge/mihomo 127.0.0.1:7897(实测无代理亦可直连推送 personal)。

## 通达信自启动链(当前 v7 + autostart-all v3 全静默)

### 启动链(2026-09-19 伪重启实测 60 秒全栈恢复)
- 计划任务 `TSP-AutoStart`(AtLogOn + SessionUnlock, principal=PAUL\WenPaul, Interactive)
  → Action: `wscript.exe autostart-silent.vbs`(UTF-16 编码, 隐藏窗口零闪现)
  → `powershell autostart-all.ps1`(v3, 互斥锁 `TSP-AutoStart-All`, 打点日志 `logs/autostart-all.log`)
  1. 启动 `D:\new_tdx_hd_test\tdxw.exe`(已跑则跳过)
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
  1. 启动 `D:\new_tdx_hd_test\tdxw.exe`(已跑则跳过),等进程出现(最多 15s)
  2. 拉起 `autostart-tdx-login.ps1`(Hidden,互斥锁 `TSP-TdxLogin-Auto`)
  3. 后端 3018(uvicorn,已在监听则跳过)
  4. 前端 3011(node vite,已在监听则跳过)
  5. 等 TQ 17709 上线(最多 5 分钟)→ POST `/api/settings/data-sources/reload` 重载能力快照
- `autostart-tdx-login.ps1`(v7)职责:关风险揭示弹窗 → 登录窗切游客页签 → BM_CLICK 登录 → 关错误弹窗/公告窗 → 等 17709
- 日志:`logs/tdx-login-auto.log`(主循环带 60s 限频 diag 日志)

### v7 关键根因(2026-09-19 实测,勿回退)
1. **风险揭示弹窗**:`D:\new_tdx_hd_test` 的通达信是「内部测试版本」,每次启动弹模态框(空标题 `#32770`,
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

## 分钟K覆盖与缺口(2026-09-24 审计)

- 分钟库 `data/kline_minute`: **1566 个日期分区, 18.7 亿行, 2020-01-02 ~ 2026-09-23**,
  0 空分区 0 解析错误(全量导入 + TQ 实时落盘合计)。
- **真实缺口(工作日): 2026-01-01 ~ 2026-04-15(约 70 个交易日)** — vipdoc .lc1 此段
  本来就没有(通达信客户端从未下载过该段分钟), 全量导入无从导出。
- 其余缺日全部对上法定节假日(春节/国庆/五一等), 正常。
- 2026-04-16 ~ 09-09 来自 TQ 盘中实时落盘(当时 TSP 盘中运行); 09-10 起未跑盘中。
- **补齐唯一路径**: 通达信客户端「功能 → 盘后数据下载」勾选 **1分钟线**(关键, 此前
  只勾了日线导致分钟停在 09-04)→ vipdoc 更新后跑
  `tools_minute_gap_backfill.py 2026-01-01 2026-09-23`(向量化后约 10-20 分钟)。
- 工具: `tools_vipdoc_watch_fill.py`(监控+达标自动回补守护)、
  `tools_tq_minute_backfill.py`(TQ 补充, 注意 TQ 分钟与 vipdoc 同源同界)。
- 全量导入 v2(tools_minute_full_import.py): BATCH=30 + 符号级断点
  (data/minutes_full_import.done, 当前 9450/14536 — 65%, 剩余标的vipdoc亦无
  2026 年段数据, 全窗口导出与已导出部分等价, 可随 vipdoc 更新后续跑)。

## 服务栈健康基线
- 前端 3011(HTTP 200)/ 后端 3018 / TQ 17709(由 tdxw 量化插件监听)
- 能力矩阵:`GET /api/settings/capability-matrix`(32 插槽);TQ 上线后
  `daily/minute/full_minute/depth5` 的 `tdx_quant` 候选应为 OK(depth5/full_minute effective=tdx_quant)
- 日K接口:`GET /api/kline/daily?symbol=600519.SH&days=10`(**symbol 必须带 .SH/.SZ 后缀;days ≥ 10**)
- `/api/health`、`/api/healthz` 均 404(无此路由)

## 通达信环境
- 量化版客户端:`D:\new_tdx_hd_test\tdxw.exe`(V7.80 内部测试版,9/11 安装;自带 TQ 插件监听 17709)
- 文档 `local-data-source-plugins.md` 中 `D:\new_tdx_mock` 已过时,现路径为 `D:\new_tdx_hd_test`(客户端自带完整 vipdoc,无需解压 hsjday.zip)
- 已知风险:测试版弹「风险揭示」且存在启动后自动退出的现象;若测试版彻底失效需换正式版+重装 TQ 插件

## 环境备忘(勿踩)
- Python 强制系统版全路径:`C:\Users\WenPaul\.local\bin\python.exe`(uv shim)
  (裸 `python` 是商店 alias 会静默失败)
- PowerShell 中文脚本必须 **UTF-8 WITH BOM + CRLF**(Write 工具不写 BOM,需系统 Python 补)
- git:`origin` 只读上游;推送用 `personal`(代理 `http.proxy=http://127.0.0.1:7897`;2026-09-28 实测无代理亦可直连)
- 通达信「盘后数据下载」需勾选 1/5 分钟线;464 只次新股分钟回补脚本 `tools_minute_sina_backfill.py` 可重跑补漏
- Tab 消息号:TCM_GETITEMW=0x133C、TCM_SETCURSEL=0x130C、TCM_GETCURSEL=0x130B(v7 脚本内已用)
