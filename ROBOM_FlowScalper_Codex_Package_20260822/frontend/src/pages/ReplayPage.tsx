// 저장 공개시장 이벤트를 backend ReplayEngine으로 재처리하고 같은 입력 프레임을 동기 재생한다.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { PriceChart } from '../components/PriceChart'
import { formatKstTime } from '../time'
import type {
  ChartData,
  HistoryRow,
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
  const [speed, setSpeed] = useState(1)

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
    if (!playing || !timeline || timeline.events.length === 0) return
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
  }, [playing, speed, timeline])

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

  return (
    <section aria-labelledby="replay-heading">
      <div className="page-heading">
        <div><p className="section-kicker">EVENT-DRIVEN</p><h2 id="replay-heading">결정적 리플레이</h2><p className="heading-help">저장된 공개시장 입력을 실제 전략·후보·PAPER 체결 경로에 다시 통과시킵니다.</p></div>
        <span className="page-note">{result ? `검증 완료 · ${result.replay_id}` : '아직 실행 결과 없음'}</span>
      </div>
      {error ? <p className="control-error" role="alert">{error}</p> : null}
      <section className="panel replay-runbar">
        <label>저장 Run<select value={selectedRun} disabled={loading || runs.length === 0} onChange={(event) => void changeRun(event.target.value)}>{runs.map((run) => <option value={run.run_id} key={run.run_id}>{run.run_id} · 이벤트 {run.market_event_count.toLocaleString()}건</option>)}</select></label>
        <label>종목<select value={selectedSymbol} disabled={!timeline} onChange={(event) => void changeSymbol(event.target.value)}>{timeline?.available_symbols.map((item) => <option value={item.symbol} key={item.symbol}>{item.symbol} · {item.event_count.toLocaleString()}건</option>)}</select></label>
        <button type="button" className="primary-button" disabled={!selectedRun || running} onClick={() => void runReplay()}>{running ? 'ReplayEngine 실행 중' : '백엔드 리플레이 실행'}</button>
      </section>
      {loading ? <div className="panel empty-state"><b>저장 Run을 확인하는 중입니다</b></div> : null}
      {!loading && runs.length === 0 ? <div className="panel empty-state"><b>리플레이할 저장 Run이 없습니다</b><p>LIVE PAPER에서 실제 공개시장 이벤트를 먼저 기록하세요.</p></div> : null}
      {timeline ? <>
        <div className="replay-layout">
          <PriceChart chart={replayChart} history={trade ? [trade] : []} replay />
          <aside className="panel replay-controls"><h3>동기 재생 제어</h3><div className="control-row"><button type="button" className="primary-button" onClick={() => setPlaying((value) => !value)} disabled={timeline.events.length === 0}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" onClick={() => setCursor((value) => Math.min(value + 1, timeline.events.length - 1))} disabled={cursor >= timeline.events.length - 1}>다음 이벤트</button></div><label>속도<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value="0.5">0.5×</option><option value="1">1×</option><option value="4">4×</option><option value="10">10×</option></select></label><label>이벤트 위치<input type="range" min="0" max={Math.max(0, timeline.events.length - 1)} value={cursor} onChange={(event) => { setCursor(Number(event.target.value)); setPlaying(false) }} /></label><dl><div><dt>현재 이벤트</dt><dd>{timeline.events.length === 0 ? '없음' : `${cursor + 1} / ${timeline.events.length}`}</dd></div><div><dt>저장 전체</dt><dd>{timeline.total_events.toLocaleString()}건{timeline.truncated ? ' · 화면 2,000건 제한' : ''}</dd></div><div><dt>종류 / 시각</dt><dd>{currentEvent?.event_type ?? '대기'}<br />{currentEvent ? `${formatKstTime(currentEvent.venue_ts_ms)} KST` : '—'}</dd></div><div><dt>bid / ask</dt><dd>{currentQuote ? `${currentQuote.bid} / ${currentQuote.ask}` : '호가 프레임 대기'}</dd></div><div><dt>스프레드</dt><dd>{spreadBps === null ? '—' : `${spreadBps.toFixed(3)} bp`}</dd></div><div><dt>전략 평가</dt><dd>{result?.strategy_evaluation_count ?? '실행 전'}회</dd></div></dl></aside>
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
