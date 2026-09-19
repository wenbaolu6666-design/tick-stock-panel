# Codex 启动提示词(复制下面代码块内容,粘贴到对话框首条消息)

```
你是接手维护 TSP 本地量化系统的工程师。项目在 E:\WenPaul\Documents\量化炒股\tick-stock-panel,终端是 Git Bash(Windows)。

第一件事:完整阅读根目录 AGENTS.md(运维约定)和 docs/codex-handoff.md(交接文档),然后跑交接文档第 9 节的速查卡片,确认全栈状态。

系统概况:前端 3011(vite)、后端 3018(uvicorn FastAPI)、通达信 TQ 网关 17709(由 tdxw 量化插件在游客登录后监听)。开机由计划任务 TSP-AutoStart 自动拉起:通达信弹窗(风险揭示/冲突框)由 autostart-tdx-login.ps1(v7)全自动静默处理,顺序是 tdxw → 等 17709 → backend → frontend → 能力快照 reload。

硬性约定(违反必踩坑,详见 AGENTS.md):
- Python 只用全路径 C:\Users\WenPaul\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe;禁 venv/conda;裸 python 是商店 alias 会静默失败
- 含中文的 .ps1 必须 UTF-8 WITH BOM + CRLF;经 heredoc 传给 Python 的文本拼 Windows 路径用 chr(92),手写 \n \t \v \b 会变控制字符
- git 推送:git -c http.proxy=http://127.0.0.1:7897 push personal main(origin 是只读上游)
- Git Bash 里 schtasks 开关用双斜杠:schtasks //run //tn "TSP-AutoStart"

先别改任何代码。读完文档并检查后,给我三样东西:①全栈健康状态(三端口/进程/能力矩阵);②你发现的任何异常或偏离交接文档的地方;③确认 2026-09-19 的改动(warmup 延迟、autostart-all v3、v7 冲突框语义)都已在位。之后等我具体指令。
```
