import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

/**
 * 板块分时(通达信 880xxx): 选板块 → 周期/日期 → 分时折线 + 明细。
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
  const minuteQuery = useQuery({
    queryKey: QK.boardMinute(boardCode, date, period),
    queryFn: () => api.boardMinute({ board_code: boardCode, date: date || undefined, period, days: 1 }),
    enabled: !!boardCode,
    staleTime: 60_000,
  })

  const boards = boardsQuery.data?.boards ?? []
  const activeBoard = boards.find(b => b.board_code === boardCode) ?? boards[0]
  const code = activeBoard?.board_code ?? ''
  if (code && code !== boardCode) setBoardCode(code)

  const rows = minuteQuery.data?.rows ?? []
  const points = useMemo(() => {
    return rows
      .map(r => {
        const t = String(r.datetime ?? '') // naive 北京墙钟
        const v = Number(r.avg ?? r.close ?? r.open ?? 0)
        const p = Number(r.preclose ?? 0)
        const pct = p ? (v - p) / p * 100 : 0
        return { t, v, p, pct }
      })
      .sort((a, b) => a.t.localeCompare(b.t))
  }, [rows])

  const chart = useMemo(() => {
    if (points.length < 2) return null
    const vals = points.map(p => p.v)
    const lo = Math.min(...vals), hi = Math.max(...vals)
    const pcts = points.map(p => p.pct)
    const plo = Math.min(...pcts, 0), phi = Math.max(...pcts, 0)
    const W = 800, H = 260, PAD = 36
    const sx = (i: number) => PAD + (i / (points.length - 1)) * (W - PAD * 2)
    const sy = (v: number) => PAD + (1 - (v - lo) / (hi - lo || 1)) * (H - PAD * 2)
    const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.v).toFixed(1)}`).join(' ')
    // 均价线
    const spath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.p).toFixed(1)}`).join(' ')
    const ticks = [0, Math.floor(points.length * 0.5), points.length - 1]
    return { path, spath, W, H, PAD, sx, sy, lo, hi, plo, phi, ticks }
  }, [points])

  return (
    <div className="p-4 lg:p-6">
      <PageHeader
        title="板块分时"
        subtitle="通达信 880xxx 概念/行业板块 · 1/5/15/30/60 分钟 · 2011-11~2026-01"
      />

      {/* 控件行 */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-muted">板块</span>
          <select
            className="rounded-btn border border-border bg-surface px-2 py-1"
            value={code}
            onChange={e => setBoardCode(e.target.value)}
          >
            {boards.slice(0, 300).map(b => (
              <option key={b.board_code} value={b.board_code}>{b.board_code} {b.board_name}</option>
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
          <span className="text-muted/60">板块清单为空 — 导入尚未覆盖此周期, 或未运行导入器</span>
        )}
      </div>

      {!boardCode && <EmptyState title="选择板块" hint="从上方下拉选择通达信概念/行业板块" />}

      {boardCode && !chart && !minuteQuery.isLoading && (
        <EmptyState title="暂无分时数据" hint="该板块此周期/日期无数据, 试试其它日期或周期" />
      )}

      {chart && (
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="mb-2 flex items-baseline gap-3">
            <span className="text-sm font-medium text-foreground">{activeBoard?.board_name ?? code}</span>
            <span className="text-xs text-muted">{code} · {period}</span>
            <span className="text-xs text-muted ml-auto">{points.length} 根</span>
          </div>
          <svg viewBox={`0 0 ${chart.W} ${chart.H}`} className="w-full" role="img" aria-label="板块分时">
            <line x1={chart.PAD} x2={chart.W - chart.PAD} y1={chart.H - chart.PAD} y2={chart.H - chart.PAD} stroke="currentColor" className="text-border" />
            <path d={chart.spath} fill="none" stroke="currentColor" className="text-muted/40" strokeWidth={1} />
            <path d={chart.path} fill="none" stroke="var(--color-accent, #60a5fa)" strokeWidth={1.5} />
            {chart.ticks.map(i => (
              <text key={i} x={chart.sx(i)} y={chart.H - 8} fontSize={9} textAnchor="middle" fill="currentColor" className="text-muted/50">
                {points[i].t.slice(11, 16) || points[i].t}
              </text>
            ))}
            <text x={4} y={chart.PAD + 4} fontSize={9} fill="currentColor" className="text-muted/60">高 {chart.hi.toFixed(2)}</text>
            <text x={4} y={chart.H - chart.PAD - 2} fontSize={9} fill="currentColor" className="text-muted/60">低 {chart.lo.toFixed(2)}</text>
          </svg>
        </div>
      )}
    </div>
  )
}
