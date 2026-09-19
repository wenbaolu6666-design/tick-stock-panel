# AI 开发入口

修改、调试或审查本仓库前，必须完整阅读并遵循根目录的 [`CONTRIBUTING.md`](CONTRIBUTING.md)。其中定义了项目架构、数据契约、数据源插件化、缓存与性能要求、测试矩阵以及 PR 复审和合并标准。

涉及代码二次开发、前端插槽、后端可替换策略、扩展注册或上游升级兼容时，还必须阅读 [`docs/secondary-development.md`](docs/secondary-development.md)。该文档区分当前已实现能力与目标扩展契约；不得根据设计示例虚构尚不存在的 API。

同时遵守以下规则：

- 先理解调用链和现有测试，再进行修改。
- 保持实现简单、改动范围最小，不处理无关问题。
- 不覆盖工作区已有修改，不虚构测试或审查结果。
- 以实际验证结果作为完成标准。

---

# TSP 本地部署运维约定(Codex/Agent 接手必读)

本仓库在本机(WenPaul)部署为**长期运行的本地量化系统**,除了上游代码约定,还必须遵循本地运维规则。接手前先通读
[docs/codex-handoff.md](docs/codex-handoff.md)(完整交接文档)与
[docs/tsp-system-state.md](docs/tsp-system-state.md)(自启动链现状,每次改动后更新)。

## 环境(硬性约定,违反必踩坑)

- 项目根:`E:\WenPaul\Documents\量化炒股\tick-stock-panel`;终端是 **Git Bash**(Windows)。
- Python 只用系统版全路径,禁止 venv/conda,裸 `python` 是商店 alias 会静默失败:
  `C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe`
- PowerShell 含中文的 `.ps1` 必须 **UTF-8 WITH BOM + CRLF**。写完必须用系统 Python 补 BOM+CRLF。
- 经 heredoc 传给 Python 的文本里 `\n` `\t` `\v` `\b` 会被转义成真控制字符 — 拼 Windows 路径
  一律用 `chr(92)` 拼接,不要手写反斜杠转义。
- 注册计划任务/写注册表的脚本同样要 BOM,否则中文路径乱码(任务 LastResult=0xFFFD0000)。
- git:`origin` 只读上游;推送用 `personal` 并带代理:
  `git -c http.proxy=http://127.0.0.1:7897 push personal main`
- Git Bash 调 Windows 命令开关用双斜杠:`schtasks //run //tn "TSP-AutoStart"`。

## 服务栈与自启动链

| 端口 | 组件 | 说明 |
|---|---|---|
| 3011 | vite 前端 | node 直启(Start-Process Hidden) |
| 3018 | uvicorn 后端 | FastAPI;启动期即用 tdx_quant 同步 instruments |
| 17709 | 通达信 TQ 网关 | tdxw 内置量化插件监听,游客登录成功后才上线 |

自启动链:计划任务 `TSP-AutoStart`(AtLogOn + SessionUnlock 双触发)
→ `autostart-silent.vbs`(wscript 零闪窗)→ `autostart-all.ps1`(v3,打点日志
`logs/autostart-all.log`)→ 启动 tdxw → 静默拉 `autostart-tdx-login.ps1`(v7 处理弹窗/游客登录)
→ **等 17709 上线** → backend(90s 校验+重试)→ frontend → 能力快照 reload。
顺序不可调换:backend 启动期即依赖 tdx_quant,TQ 未上则 backend 启动失败。

## 工作纪律

1. 改自启动链任何一环后,用 `schtasks //run //tn "TSP-AutoStart"` 全链验证,并逐 step 核对
   `logs/autostart-all.log` 与 `logs/tdx-login-auto.log`。
2. 健康检查四件套:
   `curl http://127.0.0.1:3011`;
   `curl http://127.0.0.1:3018/api/settings/capability-matrix`;
   `netstat -ano | grep -E ":(3011|3018|17709).*LISTENING"`;
   `curl "http://127.0.0.1:3018/api/kline/daily?symbol=600519.SH&days=10"`
   (symbol 必须带 .SH/.SZ 后缀,days ≥ 10;`/api/health` 不存在)。
3. 改后端前先读交接文档「已知雷区」节;**不要把 enriched warmup 移回启动期**(duckdb GIL 竞态)。
4. 通达信 UI 自动化/截图脚本里的路径只写 ASCII,否则产生乱码目录。
5. 改动提交推送 personal,并同步更新 `docs/tsp-system-state.md`。

## 已知雷区速查(详见 docs/codex-handoff.md 第 6 节)

- duckdb 同连接多线程 GIL 竞态 → 启动期禁并发查库(warmup 已延迟 90s,勿回退)。
- PowerShell EnumWindows 回调里 `$script:x +=` 静默丢数据、回调内 cmdlet 不可靠 →
  只用 `$script:ArrayList.Add()` + 回调外预取 pid 集合。
- 通达信内部测试版「风险揭示」弹窗:BM_CLICK/Enter/WM_COMMAND/真鼠标点全无效,**只有 WM_CLOSE 有效**;
  冲突框三按钮语义:是(Y)=接管杀旧实例 / 否(N)=两实例并存(禁止点)/ 取消=结束本次运行。
- 计划任务实例挂起会持互斥锁,后续触发被 IgnoreNew 静默忽略 → 先 `Stop-ScheduledTask`
  并杀 powershell/wscript 实例再触发。
- cmd /c 间接启动 node(带空格路径)实测失败 → 前端用 Start-Process 直启。
- 快速启动(Fast Startup)恢复不触发 AtLogOn → 已加 SessionUnlock 触发器兜底。
