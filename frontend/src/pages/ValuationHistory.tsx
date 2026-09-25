import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { priceColorClass } from '@/lib/format'

/**
 * 估值股本日频历史(valuation_local, 1991-2025, 5788 只):
 * 输入代码 → 表格展示 PE(TTM)/PB/股息率/股本/市值/复权因子/涨跌停价。
 */
export function ValuationHistory() {
  const [input, setInput] = useState('600519.SH')
  const [days, setDays] = useState(60)

  const q = useQuery({
    queryKey: QK.valuationHistory(input.trim(), days),
    queryFn: () => api.valuationHistory({ symbol: input.trim(), days }),
    enabled: !!input.trim(),
    staleTime: 5 * 60_000,
  })

  const rows = (q.data?.rows ?? []).slice().reverse() // 最新在上
  const latest = rows[0]

  return (
    <div className="p-4 lg:p-6">
      <PageHeader
        title="估值股本"
        subtitle="日频 PE(TTM) / PB / 股息率 / 总股本 / 流通股本 / 总市值 / 复权因子 · 1991-2025"
      />

      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-muted">股票</span>
          <input
            className="rounded-btn border border-border bg-surface px-2 py-1 w-32"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="600519.SH"
          />
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-muted">天数</span>
          <select
            className="rounded-btn border border-border bg-surface px-2 py-1"
            value={days}
            onChange={e => setDays(Number(e.target.value))}
          >
            {[20, 60, 120, 250, 500, 1000, 2500].map(d => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </label>
        {latest && (
          <span className="text-muted">
            {String(latest.date)} · PE(TTM) <b className="text-foreground">{latest.pe_ttm != null ? Number(latest.pe_ttm).toFixed(2) : '—'}</b>
            {' '}· PB <b className="text-foreground">{latest.pb != null ? Number(latest.pb).toFixed(2) : '—'}</b>
            {' '}· 总股本 {latest.total_shares_wan != null ? (Number(latest.total_shares_wan) / 10000).toFixed(2) + ' 亿股' : '—'}
          </span>
        )}
      </div>

      {!input.trim() && <EmptyState title="输入股票代码" hint="如 600519.SH / 000001.SZ" />}

      {input.trim() && !rows.length && !q.isLoading && (
        <EmptyState title="暂无数据" hint="该代码无导入记录(估值覆盖 5788 只, 1991-2025); 也可运行 tools_valuation_import.py 补充新股" />
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-card border border-border bg-surface">
          <table className="w-full text-xs">
            <thead className="text-muted/70">
              <tr>
                {['日期', 'PE(TTM)', 'PB', 'PS(TTM)', '股息率%', '换手率%', '总股本(万)', '流通(万)', '总市值(万)', '复权因子', '涨停价', '收盘'].map(h => (
                  <th key={h} className="px-3 py-2 text-right font-medium first:text-left last:text-right whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.date}-${i}`} className="border-t border-border/40">
                  <td className="px-3 py-1.5 font-mono">{String(r.date)}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.pe_ttm != null ? Number(r.pe_ttm).toFixed(2) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.pb != null ? Number(r.pb).toFixed(2) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.ps_ttm != null ? Number(r.ps_ttm).toFixed(2) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.dividend_yield_ttm != null ? Number(r.dividend_yield_ttm).toFixed(2) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.turnover_rate != null ? Number(r.turnover_rate).toFixed(2) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.total_shares_wan != null ? Number(r.total_shares_wan).toFixed(0) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.float_shares_wan != null ? Number(r.float_shares_wan).toFixed(0) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.total_value_wan != null ? Number(r.total_value_wan).toFixed(0) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.adj_factor != null ? Number(r.adj_factor).toFixed(3) : '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{r.limit_up != null ? Number(r.limit_up).toFixed(2) : '—'}</td>
                  <td className={`px-3 py-1.5 text-right font-mono ${r.close != null ? priceColorClass(Number(r.close) - Number(r.preclose ?? 0)) : ''}`}>{r.close != null ? Number(r.close).toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
