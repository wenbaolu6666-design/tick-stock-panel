# TSP 系统交接文档(Codex 版)

> 撰写时间:2026-09-19(基于当日伪重启+真实重启两轮排障会话)
> 读者:接手的 AI Agent(OpenAI Codex 等)或开发者
> 用途:不翻历史会话即可完整理解「文档体系、部署形态、怎么自启动、踩过哪些坑、如何验证、下一步做什么」
> 配套:根目录 [AGENTS.md](../AGENTS.md)(会话自动加载的约定)、[docs/tsp-system-state.md](tsp-system-state.md)(自启动链现状)

---

## 0. 文档地图与权威顺序(先读)

| 文档 | 权威范围 |
| --- | --- |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | **代码改动唯一权威**:架构与主数据流、金融数据契约(第 3 节)、插件化与能力路由矩阵契约(第 4 节)、缓存并发、验证矩阵(第 9 节)、AI 代理规则(第 13 节) |
| `docs/secondary-development.md` | 二次开发分级(L1 配置 / L2 插槽注册 / L3 改源码)、前后端扩展注册点、已实现 vs 按需扩展的边界 |
| `docs/custom-data-source.md`、`docs/plugin-development.md` | 数据源插件契约:plugin.yaml、datasets 声明、check 自检、能力路由接入 |
| `docs/strategy.md`、`backend/app/strategy/prompts/strategy-guide.md` | 策略体系(25 内置 + 三种扩展方式)、AI 生成与手写策略完整规范 |
| `docs/strategy-iteration.md` | AI 策略迭代协议:台账(LEDGER.md)/证据包/门槛判定/提示词卡片;**回测验证与上线决策永远由人完成** |
| `docs/mining.md` | 因子与策略挖掘的金融口径、防未来函数、候选显式发布永不自动上线 |
| `docs/market-phase.md` | 市场情绪周期 6 阶段口径 |
| `docs/deployment.md`、`docs/configuration.md`、`docs/features.md` | 部署(Dev/Docker)、.env 配置项、功能模块说明 |
| 本文档 + `docs/tsp-system-state.md` | **本机部署层**(自启动链/通达信/运维),与上游规范互补不替代 |

数据契约速记(细节以 CONTRIBUTING 第 3 节为准,违反即 P1):
自定义实时源 `change_pct` 与 `turnover_rate` 是**小数制**(0.0366=3.66%、0.05=5%),
enriched 的 `turnover_rate` 是**百分数**(5=5%);enriched OHLC 为**前复权**,涨停/跌停判定用
`raw_*` 原始价;窗口与回算按**交易日**,分钟 `datetime` 为北京时间 naive;财务因子严格点时
口径(公告次一交易日起生效),缺失为空值绝不填 0;股票/ETF/指数分钟分开存储路由。
禁止"数值小于 1 就乘 100"式启发转换。

## 1. 系统一句话

`E:\WenPaul\Documents\量化炒股\tick-stock-panel` 是 fork 自上游(shy3130/tickflow-stock-panel,
本地 personal remote 可推)的 A 股量化看盘/回测面板,本机长期运行:前端 3011、后端 3018、
通达信 TQ 网关 17709,开机全自动拉起,通达信弹窗全自动处理,全程无可见窗口。

## 2. 服务栈与依赖关系

```
计划任务 TSP-AutoStart (AtLogOn + SessionUnlock, principal=PAUL\WenPaul, Interactive)
   └─ wscript.exe autostart-silent.vbs        # 零闪窗包装(UTF-16 编码,勿改 ANSI)
        └─ powershell autostart-all.ps1 (v3)  # 互斥锁 TSP-AutoStart-All,打点日志 logs/autostart-all.log
             ├─ step1  E:\tdx\tdxw.exe        # 通达信(已在跑则跳过)
             ├─ step1b autostart-tdx-login.ps1(v7,CreateNoWindow,互斥锁 TSP-TdxLogin-Auto)
             │          # 自动: 关风险揭示(WM_CLOSE)→ 冲突框(取消/是)→ 游客页签 → BM_CLICK 登录
             │          #       → 关错误弹窗/公告窗 → 轮询到 17709 上线退出; 日志 logs/tdx-login-auto.log
             ├─ step2  等 17709 上线(最多 5min)   ← 顺序关键: backend 依赖 TQ
             ├─ step3  backend 3018              # cmd /c CreateNoWindow,日志重定向 backend.out/err.log
             │          # 90s 未监听: 杀掉重试一次(兜 duckdb 启动竞态/冷启动联网探测慢)
             ├─ step4  frontend 3011             # Start-Process node -WindowStyle Hidden
             └─ step5  POST /api/settings/data-sources/reload
```

依赖顺序为什么不可调换:backend 的 startup(`app/main.py` lifespan)里 daily_pipeline
立即用 tdx_quant provider 同步 instruments;TQ(17709)未上线时 provider 无效 →
`ValueError: Custom data source not found or invalid: tdx_quant` → backend 启动失败。

## 3. 通达信环境

- 客户端:`E:\tdx\tdxw.exe`(V7.80 **内部测试版**,2026-09-11 安装;TQ 量化插件内置,登录后监听 17709)。
- 注意:`docs/local-data-source-plugins.md` 里的 `D:\new_tdx_mock` 已过时,现路径就是 `E:\tdx`。
- 测试版特性:每次启动弹「风险揭示」模态框;多次启动弹三按钮冲突框。都有 v7 自动处理,无需人工。
- 已知风险:测试版偶尔启动后自动退出(`E:\tdx\T0001\tdxsys3.log` 里"开始退出TP等模块");
  若某天彻底失效,需换正式版安装包并重装 TQ 插件,这是最大的外部依赖风险。
- 盘后数据下载需勾选 1/5 分钟线;464 只次新股分钟回补脚本 `tools_minute_sina_backfill.py` 可重跑。

## 4. 部署形态与运行方式

- 后端:uvicorn(`app.main:app`),插件化数据源(`backend/app/plugins/*/plugin.yaml`,
  loader 注册 + 能力矩阵);preferences 里 daily/minute/full_minute 偏好 tdx_quant,
  adj_factor/financial 偏好 sina_astock(另见 `docs/local-data-source-plugins.md`,注意其
  `D:\new_tdx_mock` 已过时)。
- 前端:vite dev server(node 直启,非 build 部署)。
- 数据:`backend\data\`(SQLite + duckdb + parquet)。
- 能力矩阵:`GET /api/settings/capability-matrix` 共 32 插槽;TQ 在线时 daily/minute/
  full_minute/depth5/valuation/kzz/ipo 等 9 个插槽的 tdx_quant 候选应为 available。
- **运行 vs 验证要区分**:常驻服务用系统 Python 全路径直启(见 AGENTS.md);代码验证按
  CONTRIBUTING 验证矩阵用 `uv run pytest` / `uv run ruff` / `pnpm build`。

## 5. 运维手册

**启动**(等效开机):`schtasks //run //tn "TSP-AutoStart"`,然后盯 `logs/autostart-all.log`
逐 step 到 `=== autostart-all end ===`。正常全程 60~90 秒(backend 冷启动联网探测较慢)。

**停止**:杀 tdxw、按命令行匹配杀 uvicorn(3018)/ node vite(3011)、杀 autostart 脚本实例,
并 `Stop-ScheduledTask -TaskName 'TSP-AutoStart'`。按 CommandLine 匹配,不要误杀同名 MCP 进程。

**健康检查**:
```
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3011
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3018/api/settings/capability-matrix
netstat -ano | grep -E ":(3011|3018|17709).*LISTENING"
curl -s "http://127.0.0.1:3018/api/kline/daily?symbol=600519.SH&days=10"
```

**常见故障速查**:

| 症状 | 原因 | 处置 |
|---|---|---|
| 重启(关机再开)后全栈没起 | 快速启动恢复不触发 AtLogOn | 解锁桌面触发 SessionUnlock 兜底;或引导用户点「重启」 |
| 任务 lastResult=0xFFFD0000 | Action 路径乱码(注册脚本无 BOM) | 用 BOM 脚本重注册;验证 `schtasks //query //tn TSP-AutoStart //xml` |
| 触发了但日志没动静 | 旧实例挂起持互斥锁,IgnoreNew 静默忽略 | Stop-ScheduledTask + 杀 powershell/wscript 残留实例再触发 |
| autostart-all.log 里 step3 两次失败 | backend 冷启动慢(联网探测插件)或 duckdb 竞态崩 | 已放宽 90s + 重试;若仍失败看 backend.err.log 尾部栈 |
| tdxw 存在但 26MB、无窗口 | 风险揭示弹窗挂着 | v7 会自动 WM_CLOSE;手动应急:`PostMessage(hwnd,0x0010,0,0)` |
| 3018 活着但 3011 死 | node 直启失败 | 检查 `logs/frontend.err.log`;用 Start-Process 直启(勿走 cmd /c) |
| 日志全是乱码鈥/鏃ュ織 | Git Bash 显示层问题 | 内容本身 UTF-8 正常,用 Python/编辑器看 |
| 数据口径存疑(涨跌幅/换手率单位) | 跨边界转换 | 回查 CONTRIBUTING 第 3 节契约表,禁止启发式换算 |

## 6. 已知雷区(血泪记录,勿重蹈)

1. **duckdb GIL 竞态**:同一连接的多线程 cursor 在 duckdb 1.5.5 上会
   `Fatal Python error: PyEval_SaveThread` 直接杀进程。启动期 enriched warmup 线程与
   插件联网探测并发时高发。已修:main.py 里 `repo.refresh_cache(background=True)` 改为
   延迟 90 秒的 `threading.Thread(_deferred_warmup)`;**勿移回 startup 同步期**。
   另注意 main.py 曾因此出现「模块级 import threading + 函数内局部 import」的
   UnboundLocalError(局部那个在 data_integrity 块,已删,留了注释)。
2. **PowerShell EnumWindows 回调**:`$script:list += ...` 在 native 回调里静默丢数据
   (函数局部变量与 script 作用域不互通);回调内调 Get-Process 等 cmdlet 也不可靠。
   正确姿势:函数内 `$script:xxx = New-Object System.Collections.ArrayList`,
   回调里 `[void]$script:xxx.Add(...)`;进程过滤用回调外预取的 pid 集合
   (`$script:tdxPids -notcontains $p`)。参考 autostart-tdx-login.ps1 的 Get-TdxWindows。
3. **通达信测试版弹窗**:「风险揭示」对 BM_CLICK(0x00F5)/Enter/WM_COMMAND(0x0111 IDOK)/
   真鼠标点击全部免疫,只有 `WM_CLOSE (0x0010)` 有效。v7 的策略:BM_CLICK 后 2 秒复核
   IsWindowVisible,仍可见则对父窗口发 WM_CLOSE。冲突框语义:是(Y)=接管杀旧 /
   否(N)=两实例并存(绝不点)/ 取消=结束本次运行。
4. **编码四连坑**:
   - ps1/计划任务注册脚本含中文 → 必须 UTF-8 BOM(否则 GBK 误读,路径变閲忓寲鐐掕偂);
   - Python heredoc 里 `\n` `\v` `\b` `\t` 会变真控制字符写进文件(必须 chr(92) 拼);
   - vbs 文件必须 UTF-16 LE(wscript 不认 UTF-8);
   - Git Bash 显示中文乱码属显示层,用 Python/编辑器确认文件内容。
5. **cmd /c 的坑**:`cd /d "中文路径" && "带空格exe"` 对 python.exe 有效、对
   `"C:\Program Files\nodejs\node.exe"` 实测失败(node 报 Cannot find module)→
   前端用 Start-Process 直启,别绕 cmd。
6. **计划任务触发器与快速启动**:Fast Startup 恢复不产生 logon 事件 → 任务带
   LogonTrigger + SessionStateChangeTrigger(SessionUnlock)双保险;任务
   MultipleInstancesPolicy=IgnoreNew,实例挂起会卡住后续触发(见第 5 节速查)。
7. **Bash 调 Windows 工具**:PowerShell 单行内嵌 `$_` 会被 Git Bash 展开 → 复杂逻辑写
   临时 ps1 文件执行;`schtasks` 开关用 `//`;`/tmp` 对 Python(Windows 程序)不可见,
   要写 `C:\Users\WenPaul\AppData\Local\Temp`。
8. **改后端代码后必须重启 3018 才生效**(dev 模式无 --reload;自启动链拉起的是常驻进程)。
   重启方式:杀 uvicorn(按命令行匹配)后 `schtasks //run` 或手动 cmd /c 拉起。

## 7. git 与推送

- 当前分支 main;`origin` 只读上游,推 `personal`(wenbaolu6666-design/tick-stock-panel)。
- 推送必须带代理:`git -c http.proxy=http://127.0.0.1:7897 push personal main`。
- 近期提交脉络:`bb9ae36` v5 → `0a9a95f` v7 → `8866547` 状态文档 → `1e45821`
  伪重启修复 → `d38f9b9` warmup 延迟 → `905fa3c`/`f766ebf` 交接包。

## 8. 待办 / 建议(接手后可选)

1. **通达信测试版是最大外部风险**:建议找正式版(V7.80 官方版)+ 重装 TQ 量化插件;
   若弹窗/自动退出行为变化,先看 `E:\tdx\T0001\tdxsys3.log`。
2. backend 冷启动 60~90 秒主要耗在 loader 逐个联网探测插件;可考虑探测结果缓存或并行化
   (注意别加剧 duckdb 并发)。
3. 本文档与 `docs/tsp-system-state.md` 若有出入,以本文档为准并同步更新两处。
4. 计划任务 ExecutionTimeLimit=20 分钟;若自启动链变慢超时,先查此设置。
5. 上游再更新时注意 self-merge 冲突:autostart 脚本、AGENTS.md 本地段落、docs/codex-*.md
   是本地特有文件;其余按 CONTRIBUTING 复审流程走。
6. AI 策略迭代/因子挖掘任务:严格按 `docs/strategy-iteration.md`(台账协议)与
   `docs/mining.md`(显式发布纪律)执行,回测与上线决策由用户拍板。

## 9. 速查卡片(Codex 首条命令建议)

```
# 全栈状态
netstat -ano | grep -E ":(3011|3018|17709).*LISTENING"; tasklist | grep -i tdxw
# 自启动链日志
tail -20 "E:\WenPaul\Documents\量化炒股\tick-stock-panel\logs\autostart-all.log"
tail -20 "E:\WenPaul\Documents\量化炒股\tick-stock-panel\logs\tdx-login-auto.log"
# 触发自启动(等效开机)
schtasks //run //tn "TSP-AutoStart"
# 健康检查
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3011
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3018/api/settings/capability-matrix
curl -s  "http://127.0.0.1:3018/api/kline/daily?symbol=600519.SH&days=10" | head -c 200
# 代码验证(按 CONTRIBUTING)
cd backend && uv run pytest tests/path/to/test_x.py -q && uv run ruff check app/path.py
cd frontend && pnpm build
```
