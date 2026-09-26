import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type MinuteKlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { EChartsIntraday } from '@/components/EChartsIntraday'
import { fmtBigNum, fmtPct, priceColorClass } from '@/lib/format'

/**
 * 板块分时(通达信 880xxx): 选板块 → 周期/日期 → EChartsIntraday 分时图。
 * 数据: 百度盘『通达信板块_分时数据』导入(board_local), 2011-11~2026-01。
 */
export function BoardMinute() {
  const [period, setPeriod] = useState('m1')
  const [date, setDate] = useState('')
  const [boardCode, setBoardCode] = useState('')

  const periodsQuery = useQuery({ queryKey: QK.boardPeriods(), queryFn: api.boardPeriods })
  const boardsQuery = useQuery({
    queryKey: QK.boardList(period),
    queryFn: () => api.boardList(period),
    staleTime: 5 * 60_000,
  })
  const boards = boardsQuery.data?.boards ?? []
  const activeBoard = boards.find(b => b.board_code === boardCode) ?? boards[0]
  const code = activeBoard?.board_code ?? ''

  const minuteQuery = useQuery({
    queryKey: QK.boardMinute(code, date, period),
    queryFn: () => api.boardMinute({ board_code: code, date: date || undefined, period, days: 1 }),
    enabled: !!code,
    staleTime: 60_000,
  })

  const rawRows = minuteQuery.data?.rows ?? []
  // EChartsIntraday 契约: MinuteKlineRow(datetime/open/high/low/close/volume/amount)
  const data: MinuteKlineRow[] = useMemo(
    () =>
      rawRows
        .map(r => ({
          datetime: String(r.datetime ?? ''),
          open: Number(r.open ?? 0),
          high: Number(r.high ?? 0),
          low: Number(r.low ?? 0),
          close: Number(r.close ?? r.avg ?? 0),
          volume: Number(r.volume ?? 0),
          amount: r.amount != null ? Number(r.amount) : null,
        }))
        .sort((a, b) => a.datetime.localeCompare(b.datetime)),
    [rawRows],
  )
  // 板块分时的"昨收"为当日 preclose(每日不同), 取首根供涨跌幅基准
  const prevClose = data.length ? Number(rawRows[0]?.preclose ?? 0) || undefined : undefined
  const day = data.length ? data[0].datetime.slice(0, 10) : date || undefined
  const last = data[data.length - 1]
  const lastRaw = rawRows[rawRows.length - 1]

  // 无 URL date 时回显最近有数据的日期
  useEffect(() => {
    if (!date && day) setDate(day)
  }, [day, date])

  return (
    <div className="p-4 lg:p-6 space-y-4">
      <PageHeader
        title="板块分时"
        subtitle="通达信 880xxx 概念/行业板块 · 1/5/15/30/60 分钟 · 2011-11~2026-01"
      />

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-muted">板块</span>
          <select
            className="rounded-btn border border-border bg-surface px-2 py-1"
            value={code}
            onChange={e => setBoardCode(e.target.value)}
          >
            {boards.slice(0, 400).map(b => (
              <option key={b.board_code} value={b.board_code}>
                {b.board_code} {b.board_name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-muted">周期</span>
          <select
            className="rounded-btn border border-border bg-surface px-2 py-1"
            value={period}
            onChange={e => setPeriod(e.target.value)}
          >
            {(periodsQuery.data?.periods ?? ['m1']).map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-muted">日期</span>
          <input
            type="date"
            className="rounded-btn border border-border bg-surface px-2 py-1"
            value={date}
            onChange={e => setDate(e.target.value)}
          />
        </label>
        {boards.length === 0 && !boardsQuery.isLoading && (
          <span className="text-muted/60">
            板块清单为空 — 该周期尚未导入, 或未运行导入器
          </span>
        )}
      </div>

      {!code && <EmptyState title="选择板块" hint="从上方下拉选择通达信概念/行业板块" />}

      {code && data.length === 0 && !minuteQuery.isLoading && (
        <EmptyState
          title="暂无分时数据"
          hint="该板块此周期/日期无数据 — 试试其它日期或周期, 或等待导入完成"
        />
      )}

      {code && data.length > 0 && (
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="mb-2 flex flex-wrap items-baseline gap-3">
            <span className="text-sm font-medium text-foreground">{activeBoard?.board_name ?? code}</span>
            <span className="text-xs text-muted">{code} · {period}</span>
            {last && (
              <span className={`text-sm font-mono font-medium ${priceColorClass(last.close - (prevClose ?? last.close))}`}>
                {last.close.toFixed(2)}
              </span>
            )}
            {last && prevClose ? (
              <span className={`text-xs font-mono ${priceColorClass(last.close - prevClose)}`}>
                {fmtPct(((last.close - prevClose) / prevClose) * 100)}
              </span>
            ) : null}
            {lastRaw?.amount != null && (
              <span className="text-xs text-muted ml-auto">额 {fmtBigNum(Number(lastRaw.amount) * 1000)}</span>
            )}
            <span className="text-xs text-muted">{data.length} 根</span>
          </div>
          <EChartsIntraday data={data} prevClose={prevClose} date={day} height={420} showAvgLine={false} />
        </div>
      )}
    </div>
  )
}
