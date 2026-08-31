// 진행·완료·다시보기를 한 화면에서 연결하는 V6 거래 허브다.
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchJson } from '../api/client'
import { PositionList } from '../components/PositionList'
import { ReplayViewer } from '../components/ReplayViewer'
import { collapseTradeOpportunities } from '../tradeOpportunities'
import { HistoryPage } from './HistoryPage'
import type {
  DashboardData,
  HistoryRow,
  TradesResponse,
} from '../types'

type TradeTab = 'open' | 'closed' | 'replay'
export function TradesPage({ data }: { data: DashboardData }) {
  const [tab, setTab] = useState<TradeTab>('open')
  const [replayTrade, setReplayTrade] = useState<HistoryRow | null>(null)
  const [tradesLoad, setTradesLoad] = useState<{
    runId: string
    state: 'READY' | 'ERROR'
    data: TradesResponse | null
    lastUpdatedMs: number | null
  } | null>(null)
  const [refreshRevision, setRefreshRevision] = useState(0)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let disposed = false
    let inFlight = false
    let controller: AbortController | null = null
    const runId = data.status.run_id
    const load = () => {
      if (inFlight) return
      inFlight = true
      controller = new AbortController()
      void fetchJson<TradesResponse>(
        '/api/trades?run_scope=CURRENT&account_scope=ALL&profile=ALL&version_scope=CURRENT&sample_type=ALL&limit=1000',
        { signal: controller.signal },
        12_000,
      ).then((response) => {
        if (disposed) return
        if (!Array.isArray(response.opportunities)) throw new Error('invalid grouped trades response')
        setTradesLoad({ runId, state: 'READY', data: response, lastUpdatedMs: Date.now() })
      }).catch(() => {
        if (disposed || controller?.signal.aborted) return
        setTradesLoad((current) => ({
          runId,
          state: 'ERROR',
          data: current?.runId === runId ? current.data : null,
          lastUpdatedMs: current?.runId === runId ? current.lastUpdatedMs : null,
        }))
      }).finally(() => {
        inFlight = false
      })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    return () => {
      disposed = true
      controller?.abort()
      window.clearInterval(timer)
    }
  }, [data.status.run_id, refreshRevision])

  useEffect(() => {
    if (!replayTrade) return
    closeRef.current?.focus()
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setReplayTrade(null)
    }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [replayTrade])

  const openReplay = (trade: HistoryRow) => setReplayTrade(trade)
  const currentLoad = tradesLoad?.runId === data.status.run_id ? tradesLoad : null
  const groupedTrades = currentLoad?.data ?? null
  const completedRows = useMemo(
    () => groupedTrades ? collapseTradeOpportunities(groupedTrades) : [],
    [groupedTrades],
  )
  const completedLabel = groupedTrades
    ? `완료 ${groupedTrades.counts.unique_opportunities}회 · 원장 ${groupedTrades.counts.raw_result_rows}행`
    : currentLoad?.state === 'ERROR'
      ? '완료 확인 필요'
      : '완료 불러오는 중'

  return (
    <section className="trades-page" aria-labelledby="trades-heading">
      <div className="page-heading"><div><p className="section-kicker">PAPER 거래</p><h2 id="trades-heading">거래</h2><p className="heading-help">진행 중인 보호 상태, 완료 결과와 저장 이벤트 다시보기를 한곳에서 확인합니다.</p></div><span className="page-note">진행 {data.league_positions.length}건 · {completedLabel}</span></div>
      <div className="page-tabs" role="tablist" aria-label="거래 화면">
        <button type="button" role="tab" aria-selected={tab === 'open'} onClick={() => setTab('open')}>진행 중</button>
        <button type="button" role="tab" aria-selected={tab === 'closed'} onClick={() => setTab('closed')}>완료</button>
        <button type="button" role="tab" aria-selected={tab === 'replay'} onClick={() => setTab('replay')}>다시보기</button>
      </div>
      <div className="history-live-status" aria-live="polite">
        <div className="history-live-copy">
          <strong>{currentLoad?.state === 'ERROR' ? '완료 거래 갱신을 확인해야 합니다.' : groupedTrades ? '완료 거래를 5초마다 확인합니다.' : '완료 거래를 불러오는 중입니다.'}</strong>
          <small>{currentLoad?.lastUpdatedMs
            ? `마지막 확인 ${new Date(currentLoad.lastUpdatedMs).toLocaleString('ko-KR')}`
            : '정확한 6키 진입기회 묶음을 확인 전입니다.'}</small>
        </div>
        <button type="button" className="table-button" onClick={() => setRefreshRevision((revision) => revision + 1)}>지금 새로고침</button>
      </div>
      {groupedTrades?.grouping_status === 'NOT_PROVEN' ? (
        <p className="league-warning" role="status">
          {groupedTrades.source_status === 'NOT_PROVEN_RAW_LIMIT_BOUNDARY'
            ? '조회 상한에 닿아 가장 오래된 진입기회의 완전성을 확인할 수 없습니다. 표시된 결과를 전체 원장으로 간주하지 마세요.'
            : `정확한 6키 연결 근거가 없는 원장 ${groupedTrades.counts.unresolved_result_rows ?? 0}행은 진입기회 수에서 제외했습니다.`}
        </p>
      ) : null}
      {tab === 'open' ? <section className="panel wide-panel" role="tabpanel" aria-label="진행 중"><PositionList positions={data.league_positions} strategies={data.strategies} /></section> : null}
      {tab === 'closed' ? <div role="tabpanel" aria-label="완료">{groupedTrades ? <HistoryPage rows={completedRows} currentRunId={data.status.run_id} openPositionCount={data.focus_positions.length} historyScope={data.history_scope} strategies={data.strategies} providedScope="CURRENT_ALL" onReplay={openReplay} /> : <p className={currentLoad?.state === 'ERROR' ? 'error-banner' : 'bootstrap-state'} role={currentLoad?.state === 'ERROR' ? 'alert' : 'status'}>{currentLoad?.state === 'ERROR' ? '완료 거래를 불러오지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.' : '완료 거래를 불러오는 중입니다.'}</p>}</div> : null}
      {tab === 'replay' ? <div role="tabpanel" aria-label="다시보기"><ReplayViewer strategies={data.strategies} /></div> : null}
      {replayTrade ? <div className="trade-replay-layer" role="dialog" aria-modal="true" aria-label={`${replayTrade.symbol} 거래 다시보기`}>
        <div className="trade-replay-heading"><strong>{replayTrade.symbol} · 선택 거래 다시보기</strong><button ref={closeRef} type="button" className="secondary-button" onClick={() => setReplayTrade(null)}>닫기</button></div>
        <ReplayViewer key={replayTrade.trade_id} trade={replayTrade} strategies={data.strategies} />
      </div> : null}
    </section>
  )
}
