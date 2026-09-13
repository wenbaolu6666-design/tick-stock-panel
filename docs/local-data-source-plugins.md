# 本地行情插件接入说明

以下插件都遵循 TSP 内置插件契约，默认**不选中**，在「设置 → 数据源」里按数据集单独切换。

| 插件 | 目录 | 数据集 | 依赖 |
|---|---|---|---|
| tdx_local | `backend/app/plugins/tdx_local/` | daily | 无（直接读 `D:\new_tdx_mock\vipdoc\**\lday\*.day`） |
| tdx_quant | `backend/app/plugins/tdx_quant/` | daily / minute / full_minute | 无（需本机运行通达信TQ客户端，HTTP 17709） |
| mootdx | `backend/app/plugins/mootdx/` | daily / minute / full_minute / realtime | 需要 `mootdx`（含 TDX 服务器连通性） |
| sina_astock | `backend/app/plugins/sina_astock/` | adj_factor / financial | 无（httpx） |
| tencent_quote | `backend/app/plugins/tencent_quote/` | realtime | 无，但需要 TSP 已同步 instruments 维表 |
| eastmoney_astock | `backend/app/plugins/eastmoney_astock/` | daily / minute / full_minute / realtime | 无，已内置 1s 限流 |

## 已配置的通达信数据

已把下载的 4 个数据包解压到量化版客户端：

- `hsjday.zip` → `D:\new_tdx_mock\vipdoc`（沪深京日线 `.day`）
- `tdxgp.zip` → `D:\new_tdx_mock\vipdoc\cw\gp`（个股资料）
- `tdxfin.zip` → `D:\new_tdx_mock\vipdoc\cw`（财务数据 gpcw）
- `ScJyData_zbca.zip` → `D:\new_tdx_mock\vipdoc\cj`（市场交易数据）

当前偏好：

- `daily_data_provider = tdx_quant`
- `minute_data_provider = tdx_quant`
- `full_minute_data_provider = tdx_quant`
- `adj_factor_provider = sina_astock`
- `financial_data_provider = sina_astock`
- `realtime_data_provider = tencent_quote`

## 启用步骤

1. 启动 `D:\new_tdx_mock\TdxW.exe` 并登录。
2. 重启后端（或调用数据源 reload API）。
3. 在「设置 → 数据源」确认：
   - `tdx_quant` 应为可用 `ok`
   - `tdx_local` 应为可用 `ok (D:\new_tdx_mock\vipdoc)`

## 注意

- 插件声明了某个数据集后，该数据集**整个路由**到该插件；未覆盖的资产类型会按插件行为缺失或回退。
- 量化版客户端的分钟数据当前到 `2026-08-25`；要拿到最新分钟线，请在客户端里执行「盘后数据下载」。
- 东财是限流重点源，批量全市场同步会很慢；适合小样本或作为 fallback。
- `uv sync --frozen` 会移除未写进 lock 的可选依赖（如 mootdx）。`dev.ps1` / `dev.sh` 已在每次启动时自动重新安装 `plugins/*/requirements.txt`。
- 旧的 `D:\tdx` 数据已迁移完成；如要只保留量化版，可手动删除 `D:\tdx` 目录。
