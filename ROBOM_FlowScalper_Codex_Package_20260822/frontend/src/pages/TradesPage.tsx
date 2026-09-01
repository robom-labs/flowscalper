// 진행·완료·다시보기를 한 화면에서 연결하는 V6 거래 허브다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import { PositionList } from '../components/PositionList'
import { ReplayViewer } from '../components/ReplayViewer'
import { exitReasonLabel, formatDurationMs, formatUsdt, sideLabel } from '../format'
import { strategyLabel } from '../strategyPresentation'
import { formatKstDateTime } from '../time'
import { collapseTradeOpportunities } from '../tradeOpportunities'
import { HistoryPage } from './HistoryPage'
import type {
  DashboardData,
  HistoryRow,
  TradesResponse,
} from '../types'

type TradeTab = 'open' | 'closed' | 'replay'

function replayOpportunityKey(row: HistoryRow) {
  return [
    row.run_id,
    row.opportunity_id ?? row.candidate_id ?? row.signal_event_id ?? row.trade_id,
    row.strategy,
    row.symbol,
    row.side,
  ].join(':')
}

function replayPreference(row: HistoryRow) {
  return (row.profile === 'BASE' ? 4 : 0) + (row.account_scope === 'MAIN' ? 2 : 0)
}

function replayCatalogRows(response: TradesResponse | null) {
  const selected = new Map<string, HistoryRow>()
  for (const row of response ? collapseTradeOpportunities(response) : []) {
    if (!row.replay_available || row.sample_type !== 'LIVE_PUBLIC') continue
    const key = replayOpportunityKey(row)
    const current = selected.get(key)
    if (!current || replayPreference(row) > replayPreference(current)) selected.set(key, row)
  }
  return [...selected.values()].sort((left, right) => right.exit_ts_ms - left.exit_ts_ms)
}

export function TradesPage({ data }: { data: DashboardData }) {
  const [tab, setTab] = useState<TradeTab>('open')
  const [requestedReplayTrade, setRequestedReplayTrade] = useState<HistoryRow | null>(null)
  const [showRunExplorer, setShowRunExplorer] = useState(false)
  const [tradesLoad, setTradesLoad] = useState<{
    runId: string
    state: 'READY' | 'ERROR'
    data: TradesResponse | null
    lastUpdatedMs: number | null
  } | null>(null)
  const [replayLoad, setReplayLoad] = useState<{
    state: 'READY' | 'ERROR'
    data: TradesResponse | null
    lastUpdatedMs: number | null
  } | null>(null)
  const [refreshRevision, setRefreshRevision] = useState(0)

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
    if (tab !== 'replay') return
    let disposed = false
    let inFlight = false
    let controller: AbortController | null = null
    const load = () => {
      if (inFlight) return
      inFlight = true
      controller = new AbortController()
      void fetchJson<TradesResponse>(
        '/api/trades?run_scope=ALL&account_scope=ALL&profile=ALL&version_scope=ALL&sample_type=LIVE_PUBLIC&limit=1000',
        { signal: controller.signal },
        15_000,
      ).then((response) => {
        if (disposed) return
        if (!Array.isArray(response.opportunities)) throw new Error('invalid replay catalog response')
        const firstReplayTrade = replayCatalogRows(response)[0] ?? null
        setRequestedReplayTrade((current) => current ?? firstReplayTrade)
        setReplayLoad({ state: 'READY', data: response, lastUpdatedMs: Date.now() })
      }).catch(() => {
        if (disposed || controller?.signal.aborted) return
        setReplayLoad((current) => ({
          state: 'ERROR',
          data: current?.data ?? null,
          lastUpdatedMs: current?.lastUpdatedMs ?? null,
        }))
      }).finally(() => {
        inFlight = false
      })
    }
    load()
    const timer = window.setInterval(load, 15_000)
    return () => {
      disposed = true
      controller?.abort()
      window.clearInterval(timer)
    }
  }, [refreshRevision, tab])

  const openReplay = (trade: HistoryRow) => {
    setRequestedReplayTrade(trade)
    setTab('replay')
  }
  const currentLoad = tradesLoad?.runId === data.status.run_id ? tradesLoad : null
  const groupedTrades = currentLoad?.data ?? null
  const completedRows = useMemo(
    () => groupedTrades ? collapseTradeOpportunities(groupedTrades) : [],
    [groupedTrades],
  )
  const replayRows = useMemo(() => replayCatalogRows(replayLoad?.data ?? null), [replayLoad?.data])
  const selectedReplayTrade = useMemo(() => {
    if (!requestedReplayTrade) return replayRows[0] ?? null
    return replayRows.find((row) => row.trade_id === requestedReplayTrade.trade_id && row.profile === requestedReplayTrade.profile)
      ?? requestedReplayTrade
  }, [replayRows, requestedReplayTrade])
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
      {tab !== 'replay' ? <div className="history-live-status" aria-live="polite">
        <div className="history-live-copy">
          <strong>{currentLoad?.state === 'ERROR' ? '완료 거래 갱신을 확인해야 합니다.' : groupedTrades ? '완료 거래를 5초마다 확인합니다.' : '완료 거래를 불러오는 중입니다.'}</strong>
          <small>{currentLoad?.lastUpdatedMs
            ? `마지막 확인 ${new Date(currentLoad.lastUpdatedMs).toLocaleString('ko-KR')}`
            : '정확한 6키 진입기회 묶음을 확인 전입니다.'}</small>
        </div>
        <button type="button" className="table-button" onClick={() => setRefreshRevision((revision) => revision + 1)}>지금 새로고침</button>
      </div> : null}
      {tab !== 'replay' && groupedTrades?.grouping_status === 'NOT_PROVEN' ? (
        <p className="league-warning" role="status">
          {groupedTrades.source_status === 'NOT_PROVEN_RAW_LIMIT_BOUNDARY'
            ? '조회 상한에 닿아 가장 오래된 진입기회의 완전성을 확인할 수 없습니다. 표시된 결과를 전체 원장으로 간주하지 마세요.'
            : `정확한 6키 연결 근거가 없는 원장 ${groupedTrades.counts.unresolved_result_rows ?? 0}행은 진입기회 수에서 제외했습니다.`}
        </p>
      ) : null}
      {tab === 'open' ? <section className="panel wide-panel" role="tabpanel" aria-label="진행 중"><PositionList positions={data.league_positions} strategies={data.strategies} /></section> : null}
      {tab === 'closed' ? <div role="tabpanel" aria-label="완료">{groupedTrades ? <HistoryPage rows={completedRows} counts={groupedTrades.counts} currentRunId={data.status.run_id} openPositionCount={data.focus_positions.length} historyScope={data.history_scope} strategies={data.strategies} providedScope="CURRENT_ALL" onReplay={openReplay} /> : <p className={currentLoad?.state === 'ERROR' ? 'error-banner' : 'bootstrap-state'} role={currentLoad?.state === 'ERROR' ? 'alert' : 'status'}>{currentLoad?.state === 'ERROR' ? '완료 거래를 불러오지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.' : '완료 거래를 불러오는 중입니다.'}</p>}</div> : null}
      {tab === 'replay' ? <div role="tabpanel" aria-label="다시보기" className="trade-replay-tab">
        <section className="trade-replay-browser" aria-label="완료 거래 차트 다시보기">
          <aside className="replay-trade-library">
            <header><div><p className="section-kicker">완료 거래</p><h3>다시 볼 거래</h3></div><b>{replayRows.length}건</b></header>
            <p className="replay-library-help">실제 공개시장 PAPER 거래만 최신순으로 보여주며 15초마다 갱신합니다.</p>
            {replayLoad?.state === 'ERROR' ? <p className="replay-library-error" role="alert">목록 자동 갱신을 확인해야 합니다. 이미 불러온 거래는 계속 볼 수 있습니다.</p> : null}
            <div className="replay-trade-list">
              {replayRows.map((row) => {
                const selected = selectedReplayTrade?.trade_id === row.trade_id && selectedReplayTrade.profile === row.profile
                const name = strategyLabel(data.strategies.find((strategy) => strategy.strategy_id === row.strategy), row.strategy)
                return <button type="button" key={`${row.trade_id}:${row.profile}`} className={selected ? 'selected' : ''} aria-pressed={selected} onClick={() => setRequestedReplayTrade(row)}><span><b>{row.symbol} · {sideLabel(row.side)}</b><small>{name}</small></span><span><b className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</b><small>{exitReasonLabel(row.exit_reason)} · {formatDurationMs(row.holding_ms)}</small></span><time dateTime={new Date(row.entry_ts_ms).toISOString()}>{formatKstDateTime(row.entry_ts_ms)}</time></button>
              })}
              {!replayLoad ? <div className="replay-library-empty" role="status"><b>거래 목록을 불러오는 중입니다.</b></div> : null}
              {replayLoad && replayRows.length === 0 ? <div className="replay-library-empty"><b>다시 볼 공개시장 PAPER 거래가 아직 없습니다.</b><span>거래가 종료되면 여기에 자동으로 추가됩니다.</span></div> : null}
            </div>
          </aside>
          <main className="replay-player-column">
            {selectedReplayTrade ? <ReplayViewer key={`${selectedReplayTrade.trade_id}:${selectedReplayTrade.profile}`} trade={selectedReplayTrade} strategies={data.strategies} /> : <section className="panel replay-select-empty"><b>왼쪽 목록에서 다시 볼 거래를 선택하세요.</b><p>진입 전부터 실제 종료까지 차트로 재생합니다.</p></section>}
          </main>
        </section>
        <details className="panel advanced-details replay-run-explorer" onToggle={(event) => setShowRunExplorer(event.currentTarget.open)}><summary>저장 Run 직접 확인하기</summary>{showRunExplorer ? <ReplayViewer strategies={data.strategies} /> : <p>전체 저장 데이터 범위와 backend 전략 검증을 확인할 때만 여세요.</p>}</details>
      </div> : null}
    </section>
  )
}
