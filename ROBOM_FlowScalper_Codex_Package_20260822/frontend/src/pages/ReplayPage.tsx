// 저장 공개시장 이벤트를 backend ReplayEngine으로 재처리하고 같은 입력 프레임을 동기 재생한다.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PositionFocusWorkspace } from '../components/PositionFocusWorkspace'
import type { ChartOverlay } from '../components/PriceChart'
import { PriceChart } from '../components/PriceChart'
import { ReplayClock } from '../replay/ReplayClock'
import { strategyLabel } from '../strategyPresentation'
import { formatKstTime } from '../time'
import type {
  ChartData,
  FocusPosition,
  HistoryRow,
  ReplayFocusFrame,
  ReplayFocusSession,
  ReplayMarketEvent,
  ReplayResult,
  ReplayRun,
  ReplayTimeline,
} from '../types'

type Props = { trade?: HistoryRow }

function numeric(value: unknown) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function quote(event: ReplayMarketEvent | undefined) {
  if (!event) return null
  const directBid = numeric(event.data.bid)
  const directAsk = numeric(event.data.ask)
  if (directBid !== null && directAsk !== null) return { bid: directBid, ask: directAsk }
  const bids = Array.isArray(event.data.bids) ? event.data.bids : []
  const asks = Array.isArray(event.data.asks) ? event.data.asks : []
  const bidRow = Array.isArray(bids[0]) ? bids[0] : []
  const askRow = Array.isArray(asks[0]) ? asks[0] : []
  const bid = numeric(bidRow[0])
  const ask = numeric(askRow[0])
  return bid !== null && ask !== null ? { bid, ask } : null
}

export function ReplayPage({ trade }: Props) {
  const [runs, setRuns] = useState<ReplayRun[]>([])
  const [results, setResults] = useState<ReplayResult[]>([])
  const [selectedRun, setSelectedRun] = useState(trade?.run_id ?? '')
  const [timeline, setTimeline] = useState<ReplayTimeline | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState(trade?.symbol ?? '')
  const [result, setResult] = useState<ReplayResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const [cursor, setCursor] = useState(0)
  const [speed, setSpeed] = useState(5)
  const [focusSession, setFocusSession] = useState<ReplayFocusSession | null>(null)
  const clockRef = useRef<ReplayClock<ReplayFocusFrame> | null>(null)

  const loadTimeline = useCallback(async (runId: string, symbol = '') => {
    if (!runId) return
    const query = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=2000` : '?limit=2000'
    const response = await fetch(`/api/replay/${encodeURIComponent(runId)}/timeline${query}`)
    if (!response.ok) throw new Error(`timeline ${response.status}`)
    const next = (await response.json()) as ReplayTimeline
    setTimeline(next)
    setSelectedSymbol(next.symbol ?? '')
    setCursor(0)
    setPlaying(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch('/api/replay/runs').then((response) => response.json() as Promise<ReplayRun[]>),
      fetch('/api/replay/results').then((response) => response.json() as Promise<ReplayResult[]>),
    ])
      .then(async ([runRows, resultRows]) => {
        if (cancelled) return
        setRuns(runRows)
        setResults(resultRows)
        const requestedRun = trade?.run_id ?? ''
        const runId = runRows.some((run) => run.run_id === requestedRun)
          ? requestedRun
          : runRows[0]?.run_id ?? ''
        setSelectedRun(runId)
        const latest = [...resultRows].reverse().find((item) => item.source_run_id === runId) ?? null
        setResult(latest)
        if (runId) await loadTimeline(runId, trade?.symbol ?? '')
      })
      .catch(() => {
        if (!cancelled) setError('저장 Run 목록을 불러오지 못했습니다. 연결을 확인하세요.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [loadTimeline, trade?.run_id, trade?.symbol])

  useEffect(() => {
    if (!trade) return
    const controller = new AbortController()
    const query = new URLSearchParams({ trade_id: trade.trade_id, profile: trade.profile || 'BASE' })
    void fetch(`/api/replay/${encodeURIComponent(trade.run_id)}/focus?${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`focus ${response.status}`)
        const session = await response.json() as ReplayFocusSession
        setFocusSession(session)
        setCursor(0)
        setSpeed(session.default_speed)
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError('선택한 거래의 집중 리플레이를 만들지 못했습니다.')
      })
    return () => controller.abort()
  }, [trade])

  useEffect(() => {
    clockRef.current?.dispose()
    if (!focusSession) { clockRef.current = null; return }
    clockRef.current = new ReplayClock(focusSession.frames, (_frame, index, completed) => {
      setCursor(index)
      if (completed) setPlaying(false)
    })
    clockRef.current.setSpeed(focusSession.default_speed)
    clockRef.current.seek(0)
    return () => clockRef.current?.dispose()
  }, [focusSession])

  useEffect(() => {
    if (!clockRef.current) return
    clockRef.current.setSpeed(speed)
  }, [speed])

  useEffect(() => {
    if (focusSession || !playing || !timeline || timeline.events.length === 0) return
    const timer = window.setInterval(() => {
      setCursor((value) => {
        if (value >= timeline.events.length - 1) {
          setPlaying(false)
          return value
        }
        return value + 1
      })
    }, Math.max(40, 500 / speed))
    return () => window.clearInterval(timer)
  }, [focusSession, playing, speed, timeline])

  const changeRun = async (runId: string) => {
    setSelectedRun(runId)
    setResult([...results].reverse().find((item) => item.source_run_id === runId) ?? null)
    setError('')
    try {
      await loadTimeline(runId)
    } catch {
      setError('저장 이벤트 타임라인을 불러오지 못했습니다.')
    }
  }

  const changeSymbol = async (symbol: string) => {
    setError('')
    try {
      await loadTimeline(selectedRun, symbol)
    } catch {
      setError('선택 종목의 저장 이벤트를 불러오지 못했습니다.')
    }
  }

  const runReplay = async () => {
    if (!selectedRun) return
    setRunning(true)
    setError('')
    try {
      const response = await fetch(`/api/replay/${encodeURIComponent(selectedRun)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: selectedSymbol || null }),
      })
      if (!response.ok) throw new Error(`replay ${response.status}`)
      const completed = (await response.json()) as ReplayResult
      setResult(completed)
      setResults((items) => [...items, completed])
      await loadTimeline(selectedRun, selectedSymbol)
    } catch {
      setError('백엔드 리플레이가 실패했습니다. 원장 무결성과 저장 이벤트를 확인하세요.')
    } finally {
      setRunning(false)
    }
  }

  const visibleEvents = useMemo(
    () => timeline?.events.slice(0, cursor + 1) ?? [],
    [cursor, timeline?.events],
  )
  const currentEvent = visibleEvents.at(-1)
  const currentQuote = [...visibleEvents].reverse().map(quote).find(Boolean) ?? null
  const replayChart = useMemo<ChartData>(() => {
    const points = visibleEvents.flatMap((event, index) => {
      const prices = quote(event)
      if (!prices) return []
      const mid = (prices.bid + prices.ask) / 2
      return [{
        index,
        ts_ms: event.venue_ts_ms,
        bid: prices.bid,
        ask: prices.ask,
        mid,
        microprice: mid,
      }]
    })
    const currentTs = currentEvent?.venue_ts_ms ?? Number.POSITIVE_INFINITY
    return {
      symbol: (timeline?.symbol ?? selectedSymbol) || '—',
      interval: '1s',
      points,
      candles: (timeline?.candles ?? []).filter((candle) => candle.open_ts_ms <= currentTs),
      lines: { entry: null, take_profit: null, take_profit_2: null, stop: null },
      fixture: false,
    }
  }, [currentEvent?.venue_ts_ms, selectedSymbol, timeline, visibleEvents])
  const spreadBps = currentQuote
    ? ((currentQuote.ask - currentQuote.bid) / ((currentQuote.ask + currentQuote.bid) / 2)) * 10_000
    : null

  const focusFrame = focusSession?.frames[cursor] ?? null
  const focusPosition = useMemo<FocusPosition | null>(() => {
    if (!focusSession || !trade) return null
    const current = numeric(focusFrame?.data.mid) ?? numeric(focusFrame?.data.price) ?? Number(trade.exit)
    const entry = Number(trade.entry)
    const quantity = Number(trade.quantity)
    const entered = focusFrame?.phase !== 'PRE_ENTRY'
    const gross = entered ? focusSession.side === 'LONG' ? (current - entry) * quantity : (entry - current) * quantity : 0
    const fees = entered ? Number(trade.fees) : 0
    const slippage = entered ? Number(trade.slippage) : 0
    const elapsed = Math.max(0, Math.floor(((focusFrame?.ts_ms ?? focusSession.start_ts_ms) - focusSession.entry_ts_ms) / 1000))
    const stage = focusFrame?.phase ?? 'PRE_ENTRY'
    const stageKo = stage === 'PRE_ENTRY' ? '진입 전 흐름' : stage === 'OPEN' ? '익절·손절 보호 중' : '거래 종료'
    const notional = entry * quantity
    const maximumLoss = Math.abs(entry - Number(trade.initial_stop)) * quantity
    const net = gross - fees - slippage
    return {
      focus_key: `replay:${trade.trade_id}`, trade_id: trade.trade_id, candidate_id: '', run_id: trade.run_id, account_id: 'REPLAY', profile: focusSession.profile,
      venue: 'BINANCE_USDM', symbol: focusSession.symbol, side: focusSession.side, strategy: focusSession.strategy_id, strategy_id: focusSession.strategy_id, strategy_display_name_ko: strategyLabel(undefined, focusSession.strategy_id), exit_style: '저장 거래', signal_time: focusSession.entry_ts_ms, signal_ts_ms: focusSession.entry_ts_ms,
      planned_entry: trade.entry, actual_entry: entered ? trade.entry : '아직 체결 전', current_mark: String(current), initial_stop: trade.initial_stop, current_stop: trade.initial_stop,
      take_profit: trade.take_profit, take_profit_1: trade.take_profit, take_profit_2: null, quantity: trade.quantity, original_quantity: trade.quantity, remaining_quantity: stage === 'CLOSED' ? '0' : trade.quantity, notional: String(notional), notional_usdt: String(notional),
      margin_usdt: String(notional), margin_used_usdt: String(notional), risk_budget: String(maximumLoss), risk_budget_usdt: String(maximumLoss), maximum_planned_loss: String(maximumLoss), maximum_planned_loss_usdt: String(maximumLoss), remaining_planned_loss_usdt: stage === 'CLOSED' ? '0' : String(maximumLoss), effective_leverage: '1',
      gross_pnl: gross.toFixed(4), gross_pnl_usdt: gross.toFixed(4), fees: String(fees), entry_fee_usdt: String(fees), realized_exit_fees_usdt: '0', estimated_exit_fee_usdt: '0', slippage: String(slippage), slippage_usdt: String(slippage), net_pnl: net.toFixed(4), net_pnl_usdt: net.toFixed(4), return_on_margin_pct: notional > 0 ? String(net / notional * 100) : '0', account_starting_equity_usdt: '1000', account_current_equity_usdt: String(1000 + net), elapsed_seconds: elapsed,
      management_reason: focusFrame?.phase === 'PRE_ENTRY' ? '진입 전 공개시장 흐름 확인' : focusFrame?.phase === 'OPEN' ? '저장된 PAPER 포지션 진행 중' : `종료 사유 · ${trade.exit_reason}`,
      management_reason_ko: focusFrame?.phase === 'PRE_ENTRY' ? '진입 전 공개시장 흐름 확인' : focusFrame?.phase === 'OPEN' ? '저장된 PAPER 포지션 진행 중' : `종료 사유 · ${trade.exit_reason}`,
      stage, stage_ko: stageKo, data_health: '저장 이벤트 정상', recovered: false, auto_focus_eligible: false, paper_only: true, real_orders_enabled: false, auth_required: false,
    }
  }, [focusFrame, focusSession, trade])
  const focusChart = useMemo<ChartData | null>(() => {
    if (!focusSession || !focusFrame) return null
    const visibleFrames = focusSession.frames.slice(0, cursor + 1)
    const points = visibleFrames.flatMap((frame, index) => {
      const bid = numeric(frame.data.bid)
      const ask = numeric(frame.data.ask)
      const price = numeric(frame.data.mid) ?? numeric(frame.data.price)
      if (bid === null && ask === null && price === null) return []
      const resolvedBid = bid ?? price ?? ask ?? 0
      const resolvedAsk = ask ?? price ?? bid ?? 0
      const mid = price ?? (resolvedBid + resolvedAsk) / 2
      return [{ index, ts_ms: frame.ts_ms, bid: resolvedBid, ask: resolvedAsk, mid, microprice: mid }]
    })
    return {
      symbol: focusSession.symbol, interval: '3m', points,
      candles: focusSession.candles.filter((candle) => candle.open_ts_ms <= focusFrame.ts_ms),
      lines: focusFrame.phase === 'PRE_ENTRY' ? { entry: null, take_profit: null, take_profit_2: null, stop: null } : { entry: Number(trade?.entry), take_profit: Number(trade?.take_profit), take_profit_2: null, stop: Number(trade?.initial_stop) }, fixture: false,
    }
  }, [cursor, focusFrame, focusSession, trade])

  if (focusSession && focusPosition && focusChart) {
    const currentFocusFrame = focusFrame as ReplayFocusFrame
    const focusOverlay: ChartOverlay | null = currentFocusFrame.phase !== 'PRE_ENTRY' ? { key: `replay:${focusSession.trade_id}`, label: `${focusSession.strategy_id} · ${focusSession.profile}`, symbol: focusSession.symbol, side: focusSession.side, signalTime: focusSession.entry_ts_ms, entry: Number(trade?.entry), tp1: Number(trade?.take_profit), tp2: null, stop: Number(trade?.initial_stop), initialStop: Number(trade?.initial_stop), currentStop: Number(trade?.initial_stop) } : null
    const keyIndices = [...new Set(focusSession.keyframes.map((item) => item.frame_index))].sort((left, right) => left - right)
    const previousKey = [...keyIndices].reverse().find((index) => index < cursor) ?? 0
    const nextKey = keyIndices.find((index) => index > cursor) ?? focusSession.frames.length - 1
    const entryIndex = focusSession.frames.findIndex((frame) => frame.phase === 'OPEN')
    const exitIndex = focusSession.frames.findIndex((frame) => frame.phase === 'CLOSED')
    const seek = (index: number) => { clockRef.current?.seek(index); setPlaying(false) }
    const controls = <><button type="button" className="secondary-button" onClick={() => seek(0)}>처음</button><button type="button" className="secondary-button" disabled={cursor === 0} onClick={() => seek(previousKey)}>이전 핵심</button><button type="button" className="primary-button" onClick={() => {
      if (playing) clockRef.current?.pause(); else clockRef.current?.play()
      setPlaying((value) => !value)
    }}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" disabled={cursor >= focusSession.frames.length - 1} onClick={() => { clockRef.current?.step(); setPlaying(false) }}>다음 이벤트</button><button type="button" className="secondary-button" disabled={cursor >= focusSession.frames.length - 1} onClick={() => seek(nextKey)}>다음 핵심</button><button type="button" className="secondary-button" onClick={() => seek(focusSession.frames.length - 1)}>끝</button><label>속도<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{focusSession.speeds.map((value) => <option value={value} key={value}>{value}×</option>)}</select></label><label className="focus-replay-range">시간<input type="range" min="0" max={Math.max(0, focusSession.frames.length - 1)} value={cursor} onChange={(event) => seek(Number(event.target.value))} /></label><span>{cursor + 1} / {focusSession.frames.length} · {formatKstTime(currentFocusFrame.ts_ms)} KST</span><div className="focus-jumps"><button type="button" onClick={() => seek(0)}>신호</button><button type="button" disabled={entryIndex < 0} onClick={() => seek(entryIndex)}>진입</button><button type="button" disabled onClick={() => undefined}>TP1</button><button type="button" disabled={exitIndex < 0} onClick={() => seek(exitIndex)}>종료</button></div><details className="focus-timeline"><summary>핵심 과정</summary><ol>{keyIndices.map((index) => <li key={index}><button type="button" onClick={() => seek(index)}>{formatKstTime(focusSession.frames[index].ts_ms)} · {focusSession.frames[index].phase}</button></li>)}</ol></details><span className="checksum">검증 {focusSession.reconciliation.matched === null ? '샘플 UI 검수' : focusSession.reconciliation.matched ? '일치' : '확인 필요'} · {focusSession.checksum.slice(0, 12)}</span></>
    return <section className="replay-focus-page" aria-labelledby="replay-focus-heading"><div className="page-heading compact"><div><p className="section-kicker">TRADE REPLAY</p><h2 id="replay-focus-heading">{focusSession.symbol} 거래 집중 재생</h2></div><span className={speed === 80 ? 'page-note fast-forward' : 'page-note'}>{speed === 80 ? '빨리감기 · 80배속' : 'PAPER · 저장 이벤트만'}</span></div><PositionFocusWorkspace mode={currentFocusFrame.phase === 'CLOSED' ? 'CLOSED_REVIEW' : 'REPLAY'} position={focusPosition} chart={focusChart} overlay={focusOverlay} history={trade && currentFocusFrame.phase === 'CLOSED' ? [trade] : []} controls={controls} /></section>
  }

  return (
    <section aria-labelledby="replay-heading">
      <div className="page-heading">
        <div><p className="section-kicker">PAST PLAYBACK</p><h2 id="replay-heading">과거 데이터 다시 보기</h2><p className="heading-help">저장한 시장 데이터를 같은 조건으로 다시 돌려 판단 과정을 확인합니다.</p></div>
        <span className="page-note">{result ? `검증 완료 · ${result.replay_id}` : '아직 실행 결과 없음'}</span>
      </div>
      {error ? <p className="control-error" role="alert">{error}</p> : null}
      <section className="panel replay-runbar">
        <label>저장 Run<select value={selectedRun} disabled={loading || runs.length === 0} onChange={(event) => void changeRun(event.target.value)}>{runs.map((run) => <option value={run.run_id} key={run.run_id}>{run.run_id} · {run.market_event_count === null ? '시장 데이터 저장됨' : `이벤트 ${run.market_event_count.toLocaleString()}건`}</option>)}</select></label>
        <label>종목<select value={selectedSymbol} disabled={!timeline} onChange={(event) => void changeSymbol(event.target.value)}>{timeline?.available_symbols.map((item) => <option value={item.symbol} key={item.symbol}>{item.symbol} · {item.event_count === null ? '저장 데이터 있음' : `${item.event_count.toLocaleString()}건`}</option>)}</select></label>
        <button type="button" className="primary-button" disabled={!selectedRun || running} onClick={() => void runReplay()}>{running ? '다시 확인하는 중' : '이 기록 다시 확인하기'}</button>
      </section>
      {loading ? <div className="panel empty-state"><b>저장 Run을 확인하는 중입니다</b></div> : null}
      {!loading && runs.length === 0 ? <div className="panel empty-state"><b>리플레이할 저장 Run이 없습니다</b><p>LIVE PAPER에서 실제 공개시장 이벤트를 먼저 기록하세요.</p></div> : null}
      {timeline ? <>
        <div className="replay-layout">
          <PriceChart chart={replayChart} history={trade ? [trade] : []} replay />
          <aside className="panel replay-controls"><h3>동기 재생 제어</h3><div className="control-row"><button type="button" className="primary-button" onClick={() => setPlaying((value) => !value)} disabled={timeline.events.length === 0}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" onClick={() => setCursor((value) => Math.min(value + 1, timeline.events.length - 1))} disabled={cursor >= timeline.events.length - 1}>다음 이벤트</button></div><label>속도<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{[0.5, 1, 2, 5, 10, 20, 40, 80].map((value) => <option value={value} key={value}>{value}×</option>)}</select></label><label>이벤트 위치<input type="range" min="0" max={Math.max(0, timeline.events.length - 1)} value={cursor} onChange={(event) => { setCursor(Number(event.target.value)); setPlaying(false) }} /></label><dl><div><dt>현재 이벤트</dt><dd>{timeline.events.length === 0 ? '없음' : `${cursor + 1} / ${timeline.events.length}`}</dd></div><div><dt>저장 전체</dt><dd>{timeline.total_events === null ? '2,000건 이상 저장됨' : `${timeline.total_events.toLocaleString()}건`}{timeline.truncated ? ' · 화면 2,000건 제한' : ''}</dd></div><div><dt>종류 / 시각</dt><dd>{currentEvent?.event_type ?? '대기'}<br />{currentEvent ? `${formatKstTime(currentEvent.venue_ts_ms)} KST` : '—'}</dd></div><div><dt>bid / ask</dt><dd>{currentQuote ? `${currentQuote.bid} / ${currentQuote.ask}` : '호가 프레임 대기'}</dd></div><div><dt>스프레드</dt><dd>{spreadBps === null ? '—' : `${spreadBps.toFixed(3)} bp`}</dd></div><div><dt>전략 평가</dt><dd>{result?.strategy_evaluation_count ?? '실행 전'}회</dd></div></dl></aside>
        </div>
        <section className="replay-proof-grid">
          <article className="panel"><span>입력 Checksum</span><b className="checksum">{result?.checksum ?? '백엔드 리플레이를 실행하면 표시됩니다.'}</b></article>
          <article className="panel"><span>종단간 결과</span><b>{result ? `후보 ${result.candidate_plan_count} · 주계좌 ${result.main_trade_count} · shadow ${result.shadow_trade_count}` : '실행 전'}</b><small>{result ? `실제 주문 ${result.real_orders_enabled ? '위험' : '0'} · 인증 경로 ${result.auth_required ? '위험' : '0'}` : '저장 이벤트 탐색만 수행 중'}</small></article>
        </section>
        {result ? <details className="panel advanced-details replay-diagnostics"><summary>결정 경로와 최종 상태 보기</summary><div className="diagnostic-columns"><div><h3>결정 경로</h3><ol>{result.decision_path.slice(-20).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ol></div><div><h3>최종 상태</h3><p>{result.final_state}</p></div></div></details> : null}
      </> : null}
    </section>
  )
}
