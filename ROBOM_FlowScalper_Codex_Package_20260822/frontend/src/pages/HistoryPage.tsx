// 불변 원장의 실제 완료 거래를 LIVE·DEMO로 구분하고 체결 상세와 리플레이 동선을 제공한다.
import { useMemo, useState } from 'react'
import { exitReasonLabel, formatDurationMs, formatPrice, formatQuantity, formatUsdt } from '../format'
import type { HistoryRow } from '../types'

type Props = { rows: HistoryRow[]; currentRunId: string; onReplay: (trade: HistoryRow) => void }
type Filter = 'ALL' | 'LIVE_PUBLIC' | 'OFFLINE_FIXTURE'
type RunFilter = 'CURRENT' | 'ALL'

export function HistoryPage({ rows, currentRunId, onReplay }: Props) {
  const [filter, setFilter] = useState<Filter>('ALL')
  const [runFilter, setRunFilter] = useState<RunFilter>('CURRENT')
  const [selectedTrade, setSelected] = useState<HistoryRow | null>(null)
  const filtered = useMemo(
    () => rows.filter((row) => (
      (runFilter === 'ALL' || row.run_id === currentRunId)
      && (filter === 'ALL' || row.sample_type === filter)
    )),
    [currentRunId, filter, rows, runFilter],
  )
  const selected = selectedTrade
    && filtered.some((row) => row.trade_id === selectedTrade.trade_id)
    ? selectedTrade
    : null
  return (
    <section aria-labelledby="history-heading">
      <div className="page-heading">
        <div><p className="section-kicker">TRADE HISTORY</p><h2 id="history-heading">거래 기록</h2><p className="heading-help">종료된 모의거래만 보관하고 표시합니다.</p></div>
        <div className="history-heading-filters"><label className="inline-filter">Run 범위<select value={runFilter} onChange={(event) => setRunFilter(event.target.value as RunFilter)}><option value="CURRENT">이번 Run</option><option value="ALL">전체 Run</option></select></label><label className="inline-filter">기록 구분<select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}><option value="ALL">전체</option><option value="LIVE_PUBLIC">공개시장 모의거래</option><option value="OFFLINE_FIXTURE">샘플 거래</option></select></label></div>
      </div>
      <div className={selected ? 'history-layout drawer-open' : 'history-layout'}>
        <section className="panel wide-panel table-scroll">
          <table className="history-table"><thead><tr><th>Run / 거래</th><th>종목</th><th>전략</th><th>체결→종료</th><th>종료사유</th><th>총손익</th><th>비용</th><th>순손익</th><th>보유</th><th>보기</th></tr></thead>
            <tbody>{filtered.map((row) => <tr key={row.trade_id}><td><strong>{row.trade_id}</strong><small>{row.run_id}<br />{row.sample_type === 'LIVE_PUBLIC' ? '공개시장 PAPER' : '오프라인 DEMO'}</small></td><td>{row.symbol}<small>{row.side}</small></td><td>{row.strategy}<small>{row.profile}</small></td><td>{formatPrice(row.entry)} → {formatPrice(row.exit)}</td><td>{exitReasonLabel(row.exit_reason)}<small>{row.exit_reason}</small></td><td>{formatUsdt(row.gross_pnl)}</td><td>수수료 {formatUsdt(row.fees)}<br />슬리피지 {formatUsdt(row.slippage)}</td><td className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</td><td>{formatDurationMs(row.holding_ms)}</td><td><div className="table-actions"><button type="button" className="table-button" onClick={() => setSelected(row)}>상세</button><button type="button" className="table-button" onClick={() => onReplay(row)}>재생</button></div></td></tr>)}</tbody>
          </table>
          {filtered.length === 0 ? <p className="empty-copy">이번 Run에는 아직 완료된 PAPER 거래가 없습니다. 과거 기록은 `전체 Run`에서 볼 수 있습니다.</p> : null}
        </section>
        {selected ? <aside className="panel trade-drawer" aria-labelledby="trade-detail-title"><div className="panel-title"><h3 id="trade-detail-title">거래 상세</h3><button type="button" className="close-button" aria-label="거래 상세 닫기" onClick={() => setSelected(null)}>닫기</button></div><dl className="detail-list"><div><dt>거래 ID</dt><dd>{selected.trade_id}</dd></div><div><dt>종목 / 방향</dt><dd>{selected.symbol} · {selected.side}</dd></div><div><dt>전략</dt><dd>{selected.strategy}</dd></div><div><dt>수량</dt><dd>{formatQuantity(selected.quantity)}</dd></div><div><dt>실제 진입</dt><dd>{formatPrice(selected.entry)}</dd></div><div><dt>초기 손절</dt><dd>{formatPrice(selected.initial_stop)}</dd></div><div><dt>목표가</dt><dd>{formatPrice(selected.take_profit)}</dd></div><div><dt>실제 종료</dt><dd>{formatPrice(selected.exit)}</dd></div><div><dt>종료 사유</dt><dd>{exitReasonLabel(selected.exit_reason)}</dd></div><div><dt>보유시간</dt><dd>{formatDurationMs(selected.holding_ms)}</dd></div><div><dt>수수료 / 슬리피지</dt><dd>{formatUsdt(selected.fees)} / {formatUsdt(selected.slippage)}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(selected.net_pnl, { signed: true })}</dd></div></dl><button type="button" className="primary-button full-width" onClick={() => onReplay(selected)}>이 Run 리플레이 열기</button></aside> : null}
      </div>
    </section>
  )
}
