// 저장 공개시장 이벤트를 backend ReplayEngine으로 재처리하고 같은 입력 프레임을 동기 재생한다.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PositionFocusWorkspace } from '../components/PositionFocusWorkspace'
import type { ChartOverlay } from '../components/PriceChart'
import { PriceChart } from '../components/PriceChart'
import { costProfileLabel, exitReasonLabel } from '../format'
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
  ReplayOperation,
  ReplayResult,
  ReplayRun,
  ReplayTimeline,
} from '../types'

type Props = { trade?: HistoryRow }

const ACTIVE_REPLAY_STATES = new Set<ReplayOperation['state']>([
  'REQUESTED', 'PREPARING', 'PROCESSING', 'CANCELLING',
])
const INTERACTIVE_TIMELINE_LIMIT = 100

function replayOperationActive(operation: ReplayOperation | null) {
  return operation !== null && ACTIVE_REPLAY_STATES.has(operation.state)
}

function replayResultScopeSymbol(result: ReplayResult) {
  const explicit = result.scope_symbol?.trim().toUpperCase()
  if (explicit) return explicit
  const symbols = Object.keys(result.symbol_counts ?? {})
  return symbols.length === 1 ? symbols[0].trim().toUpperCase() : null
}

function matchingReplayResult(
  results: ReplayResult[],
  runId: string,
  symbol: string,
  eventLimit: number | null | undefined,
) {
  const normalizedSymbol = symbol.trim().toUpperCase()
  if (!runId || !normalizedSymbol) return null
  return [...results].reverse().find((item) => (
    item.source_run_id === runId && replayResultScopeSymbol(item) === normalizedSymbol
    && (eventLimit == null || item.event_count === eventLimit)
  )) ?? null
}

function elapsedLabel(startedTsMs: number, nowTsMs: number) {
  const seconds = Math.max(0, Math.floor((nowTsMs - startedTsMs) / 1_000))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes > 0 ? `${minutes}분 ${remainder}초` : `${remainder}초`
}

function numeric(value: unknown) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

async function replayErrorMessage(response: Response, fallback: string) {
  try {
    const payload = await response.json() as { detail?: { error_message_ko?: unknown } }
    const message = payload.detail?.error_message_ko
    return typeof message === 'string' && message ? message : fallback
  } catch {
    return fallback
  }
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback
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
  const [loading, setLoading] = useState(true)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [operation, setOperation] = useState<ReplayOperation | null>(null)
  const [operationNow, setOperationNow] = useState(0)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const [cursor, setCursor] = useState(0)
  const [speed, setSpeed] = useState(5)
  const [focusSession, setFocusSession] = useState<ReplayFocusSession | null>(null)
  const [focusLoading, setFocusLoading] = useState(Boolean(trade))
  const [focusAttempt, setFocusAttempt] = useState(0)
  const clockRef = useRef<ReplayClock<ReplayFocusFrame> | null>(null)
  const previewRequestRef = useRef(0)
  const result = useMemo(
    () => running
      ? null
      : matchingReplayResult(results, selectedRun, selectedSymbol, timeline?.total_events),
    [results, running, selectedRun, selectedSymbol, timeline?.total_events],
  )

  const loadPreview = useCallback(async (runId: string, symbol = '') => {
    if (!runId) return
    const requestId = previewRequestRef.current + 1
    previewRequestRef.current = requestId
    setPreviewLoading(true)
    try {
      const query = new URLSearchParams({ candle_limit: '500' })
      if (symbol) query.set('symbol', symbol)
      const response = await fetch(`/api/replay/${encodeURIComponent(runId)}/preview?${query}`)
      if (!response.ok) throw new Error(await replayErrorMessage(response, '저장 데이터 미리보기를 불러오지 못했습니다.'))
      const next = (await response.json()) as ReplayTimeline
      if (requestId !== previewRequestRef.current) return
      setTimeline(next)
      setSelectedSymbol(next.symbol ?? '')
      setCursor(0)
      setPlaying(false)
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(false)
    }
  }, [])

  const loadTimeline = useCallback(async (runId: string, symbol = '') => {
    if (!runId) return
    setTimelineLoading(true)
    try {
      const query = symbol
        ? `?symbol=${encodeURIComponent(symbol)}&limit=${INTERACTIVE_TIMELINE_LIMIT}`
        : `?limit=${INTERACTIVE_TIMELINE_LIMIT}`
      const response = await fetch(`/api/replay/${encodeURIComponent(runId)}/timeline${query}`)
      if (!response.ok) throw new Error(await replayErrorMessage(response, '저장 이벤트 타임라인을 불러오지 못했습니다.'))
      const next = (await response.json()) as ReplayTimeline
      setTimeline(next)
      setSelectedSymbol(next.symbol ?? '')
      setCursor(0)
      setPlaying(false)
    } finally {
      setTimelineLoading(false)
    }
  }, [])

  useEffect(() => {
    if (trade) return
    let cancelled = false
    const resultRowsPromise = fetch('/api/replay/results')
      .then((response) => response.json() as Promise<ReplayResult[]>)
    Promise.all([
      fetch('/api/replay/runs').then((response) => response.json() as Promise<ReplayRun[]>),
      fetch('/api/replay/operations/current').then((response) => response.json() as Promise<ReplayOperation | null>),
    ])
      .then(([runRows, currentOperation]) => {
        if (cancelled) return
        setRuns(runRows)
        if (replayOperationActive(currentOperation)) {
          setOperation(currentOperation)
          setRunning(true)
          setOperationNow(Date.now())
        }
        const runId = runRows[0]?.run_id ?? ''
        setSelectedRun(runId)
        setLoading(false)
        if (runId) {
          void loadPreview(runId).catch((reason: unknown) => {
            if (!cancelled) {
              setError(errorMessage(reason, '저장 데이터 미리보기를 불러오지 못했습니다.'))
            }
          })
        }
        void resultRowsPromise.then((resultRows) => {
          if (cancelled) return
          setResults(resultRows)
        }).catch(() => {
          if (!cancelled) setError('과거 전략 검증 결과는 늦게 불러오는 중입니다. 저장 Run 미리보기는 계속 사용할 수 있습니다.')
        })
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(errorMessage(reason, '저장 Run 목록을 불러오지 못했습니다. 연결을 확인하세요.'))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
      previewRequestRef.current += 1
    }
  }, [loadPreview, trade])

  useEffect(() => {
    if (!replayOperationActive(operation)) return
    const timer = window.setInterval(() => setOperationNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [operation])

  const operationId = operation?.operation_id
  const operationState = operation?.state

  useEffect(() => {
    if (!operationId || !operationState || !ACTIVE_REPLAY_STATES.has(operationState)) return
    const controller = new AbortController()
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/replay/operations/${encodeURIComponent(operationId)}`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error(await replayErrorMessage(response, '저장 Run 검증 상태를 확인하지 못했습니다.'))
        const next = await response.json() as ReplayOperation
        setOperation(next)
        const active = replayOperationActive(next)
        setRunning(active)
        if (next.state === 'COMPLETED' && next.result) {
          setResults((items) => items.some((item) => item.replay_id === next.result?.replay_id)
            ? items
            : [...items, next.result as ReplayResult])
        } else if (next.state === 'FAILED_RETRYABLE' || next.state === 'FAILED_BLOCKED') {
          setError(next.error_message_ko || '저장 Run 전략 검증을 완료하지 못했습니다.')
        }
      } catch (reason: unknown) {
        if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
          setRunning(false)
          setError(errorMessage(reason, '저장 Run 검증 상태를 확인하지 못했습니다.'))
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1_000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [operationId, operationState])

  useEffect(() => {
    if (!trade) return
    const controller = new AbortController()
    const query = new URLSearchParams({ trade_id: trade.trade_id, profile: trade.profile || 'BASE' })
    void fetch(`/api/replay/${encodeURIComponent(trade.run_id)}/focus?${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await replayErrorMessage(response, '선택한 거래의 집중 리플레이를 만들지 못했습니다.'))
        const session = await response.json() as ReplayFocusSession
        setFocusSession(session)
        setCursor(0)
        setSpeed(session.default_speed)
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(errorMessage(reason, '선택한 거래의 집중 리플레이를 만들지 못했습니다.'))
      })
      .finally(() => {
        if (!controller.signal.aborted) setFocusLoading(false)
      })
    return () => controller.abort()
  }, [focusAttempt, trade])

  useEffect(() => {
    clockRef.current?.dispose()
    if (!focusSession) { clockRef.current = null; return }
    clockRef.current = new ReplayClock(focusSession.frames, (_frame, index, completed) => {
      setCursor(index)
      if (completed) setPlaying(false)
    })
    // 긴 무거래 구간도 버튼이 멈춘 것처럼 보이지 않도록 프레임 간 대기를 최대 1초(5배속 기준)로 압축한다.
    clockRef.current.setMaximumFrameGap(5_000)
    clockRef.current.setSpeed(focusSession.default_speed)
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
    setSelectedSymbol('')
    setTimeline(null)
    setPlaying(false)
    setError('')
    try {
      await loadPreview(runId)
    } catch (reason: unknown) {
      setError(errorMessage(reason, '저장 데이터 미리보기를 불러오지 못했습니다.'))
    }
  }

  const changeSymbol = async (symbol: string) => {
    setError('')
    try {
      await loadPreview(selectedRun, symbol)
    } catch (reason: unknown) {
      setError(errorMessage(reason, '선택 종목의 저장 데이터 미리보기를 불러오지 못했습니다.'))
    }
  }

  const loadSelectedTimeline = async () => {
    setError('')
    try {
      await loadTimeline(selectedRun, selectedSymbol)
    } catch (reason: unknown) {
      setError(errorMessage(reason, '선택 종목의 정밀 이벤트를 불러오지 못했습니다.'))
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
        body: JSON.stringify({
          symbol: selectedSymbol || null,
          event_limit: timeline?.total_events ?? undefined,
        }),
      })
      if (!response.ok) throw new Error(await replayErrorMessage(response, '백엔드 리플레이가 실패했습니다. 원장 무결성과 저장 이벤트를 확인하세요.'))
      const requested = (await response.json()) as ReplayOperation
      setOperation(requested)
      setOperationNow(Date.now())
    } catch (reason: unknown) {
      setRunning(false)
      setError(errorMessage(reason, '백엔드 리플레이가 실패했습니다. 원장 무결성과 저장 이벤트를 확인하세요.'))
    }
  }

  const cancelReplay = async () => {
    if (!operation || !replayOperationActive(operation)) return
    setError('')
    try {
      const response = await fetch(
        `/api/replay/operations/${encodeURIComponent(operation.operation_id)}`,
        { method: 'DELETE' },
      )
      if (!response.ok) throw new Error(await replayErrorMessage(response, '저장 Run 검증을 취소하지 못했습니다.'))
      setOperation(await response.json() as ReplayOperation)
    } catch (reason: unknown) {
      setError(errorMessage(reason, '저장 Run 검증을 취소하지 못했습니다.'))
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
    const levels = focusSession.levels
    const current = numeric(focusFrame?.data.mid) ?? numeric(focusFrame?.data.price) ?? Number(trade.exit)
    const entry = Number(trade.entry)
    const quantity = Number(trade.quantity)
    const entered = focusFrame?.phase !== 'PRE_ENTRY'
    const closed = focusFrame?.phase === 'CLOSED'
    const gross = entered ? focusSession.side === 'LONG' ? (current - entry) * quantity : (entry - current) * quantity : 0
    const recordedFees = entered ? Number(trade.fees) : 0
    const entryFill = focusSession.fills.find((fill) => fill.intent === 'ENTRY')
    const exitFill = focusSession.fills.find((fill) => fill.intent === 'EXIT')
    const allocatedEntryFee = numeric(entryFill?.fee_usdt)
    const allocatedExitFee = numeric(exitFill?.fee_usdt)
    const hasAllocatedFees = allocatedEntryFee !== null && allocatedExitFee !== null
    const entryFee = entered
      ? hasAllocatedFees ? allocatedEntryFee ?? 0 : recordedFees
      : 0
    const realizedExitFee = closed && hasAllocatedFees ? allocatedExitFee ?? 0 : 0
    const estimatedExitFee = entered && !closed && hasAllocatedFees ? allocatedExitFee ?? 0 : 0
    const entrySlippage = numeric(entryFill?.slippage_usdt)
    const exitSlippage = numeric(exitFill?.slippage_usdt)
    const hasAllocatedSlippage = entrySlippage !== null && exitSlippage !== null
    const slippage = entered
      ? hasAllocatedSlippage
        ? (entrySlippage ?? 0) + (closed ? exitSlippage ?? 0 : 0)
        : Number(trade.slippage)
      : 0
    const fees = entryFee + realizedExitFee + estimatedExitFee
    const elapsed = Math.max(0, Math.floor(((focusFrame?.ts_ms ?? focusSession.start_ts_ms) - focusSession.entry_ts_ms) / 1000))
    const stage = focusFrame?.phase ?? 'PRE_ENTRY'
    const stageKo = stage === 'PRE_ENTRY' ? '진입 전 흐름' : stage === 'OPEN' ? '익절·손절 보호 중' : '거래 종료'
    const notional = entry * quantity
    const maximumLoss = Math.abs(entry - Number(trade.initial_stop)) * quantity
    const net = gross - fees - slippage
    return {
      focus_key: `replay:${trade.trade_id}`, trade_id: trade.trade_id, candidate_id: '', run_id: trade.run_id, account_id: 'REPLAY', profile: focusSession.profile,
      venue: 'BINANCE_USDM', symbol: focusSession.symbol, side: focusSession.side, strategy: focusSession.strategy_id, strategy_id: focusSession.strategy_id, strategy_display_name_ko: strategyLabel(undefined, focusSession.strategy_id), exit_style: '저장 거래', signal_time: focusSession.entry_ts_ms, signal_ts_ms: focusSession.entry_ts_ms,
      planned_entry: levels.entry, actual_entry: entered ? levels.entry : '아직 체결 전', current_mark: String(current), initial_stop: levels.initial_stop, current_stop: levels.initial_stop,
      take_profit: levels.take_profit_1, take_profit_1: levels.take_profit_1, take_profit_2: levels.take_profit_2, quantity: trade.quantity, original_quantity: trade.quantity, remaining_quantity: stage === 'CLOSED' ? '0' : trade.quantity, notional: String(notional), notional_usdt: String(notional),
      margin_usdt: String(notional), margin_used_usdt: String(notional), risk_budget: String(maximumLoss), risk_budget_usdt: String(maximumLoss), maximum_planned_loss: String(maximumLoss), maximum_planned_loss_usdt: String(maximumLoss), remaining_planned_loss_usdt: stage === 'CLOSED' ? '0' : String(maximumLoss), effective_leverage: '1',
      gross_pnl: gross.toFixed(4), gross_pnl_usdt: gross.toFixed(4), fees: String(fees), entry_fee_usdt: String(entryFee), realized_exit_fees_usdt: String(realizedExitFee), estimated_exit_fee_usdt: String(estimatedExitFee), slippage: String(slippage), slippage_usdt: String(slippage), net_pnl: net.toFixed(4), net_pnl_usdt: net.toFixed(4), return_on_margin_pct: notional > 0 ? String(net / notional * 100) : '0', account_starting_equity_usdt: '1000', account_current_equity_usdt: String(1000 + net), elapsed_seconds: elapsed,
      management_reason: focusFrame?.phase === 'PRE_ENTRY' ? '진입 전 공개시장 흐름 확인' : focusFrame?.phase === 'OPEN' ? '저장된 PAPER 포지션 진행 중' : `종료 이유 · ${exitReasonLabel(trade.exit_reason)}`,
      management_reason_ko: focusFrame?.phase === 'PRE_ENTRY' ? '진입 전 공개시장 흐름 확인' : focusFrame?.phase === 'OPEN' ? '저장된 PAPER 포지션 진행 중' : `종료 이유 · ${exitReasonLabel(trade.exit_reason)}`,
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
      lines: focusFrame.phase === 'PRE_ENTRY' ? { entry: null, take_profit: null, take_profit_2: null, stop: null } : { entry: Number(focusSession.levels.entry), take_profit: Number(focusSession.levels.take_profit_1), take_profit_2: numeric(focusSession.levels.take_profit_2), stop: Number(focusSession.levels.initial_stop) }, fixture: false,
    }
  }, [cursor, focusFrame, focusSession])

  if (trade && focusLoading && !focusSession) {
    return <section aria-labelledby="replay-focus-loading"><div className="page-heading"><div><p className="section-kicker">거래 다시보기</p><h2 id="replay-focus-loading">{trade.symbol} 거래 차트 준비 중</h2><p className="heading-help">선택한 거래 앞뒤의 저장 이벤트만 읽고 있습니다. 공개시장 관찰과 PAPER 관리는 계속 작동합니다.</p></div><span className="page-note">실제 주문 없음</span></div><div className="panel empty-state" role="status"><b>진입·익절·손절·실제 종료 위치를 구성하고 있습니다</b><p>전체 전략 검증은 이 화면과 분리된 백그라운드 작업에서 실행합니다.</p></div></section>
  }

  if (trade && !focusSession && error) {
    return <section aria-labelledby="replay-focus-error"><div className="page-heading"><div><p className="section-kicker">거래 다시보기</p><h2 id="replay-focus-error">{trade.symbol} 거래 차트를 열지 못했습니다</h2><p className="heading-help">거래 기록은 원장에 남아 있으며 다시보기 화면만 준비하지 못했습니다.</p></div><span className="page-note">실제 주문 없음</span></div><div className="panel empty-state" role="alert"><b>{error}</b><p>공개시장 관찰과 PAPER 관리는 계속 작동합니다.</p><button type="button" className="primary-button" onClick={() => { setFocusLoading(true); setFocusSession(null); setError(''); setFocusAttempt((value) => value + 1) }}>거래 차트 다시 시도</button></div></section>
  }

  if (focusSession && focusPosition && focusChart) {
    const currentFocusFrame = focusFrame as ReplayFocusFrame
    const focusOverlay: ChartOverlay | null = currentFocusFrame.phase !== 'PRE_ENTRY' ? { key: `replay:${focusSession.trade_id}`, label: `${strategyLabel(undefined, focusSession.strategy_id)} · ${costProfileLabel(focusSession.profile)}`, symbol: focusSession.symbol, side: focusSession.side, signalTime: focusSession.levels.signal_ts_ms, entry: Number(focusSession.levels.entry), tp1: Number(focusSession.levels.take_profit_1), tp2: numeric(focusSession.levels.take_profit_2), stop: Number(focusSession.levels.initial_stop), initialStop: Number(focusSession.levels.initial_stop), currentStop: Number(focusSession.levels.initial_stop), status: currentFocusFrame.phase === 'CLOSED' ? 'CLOSED' : 'OPEN' } : null
    const keyIndices = [...new Set(focusSession.keyframes.map((item) => item.frame_index))].sort((left, right) => left - right)
    const previousKey = [...keyIndices].reverse().find((index) => index < cursor) ?? 0
    const nextKey = keyIndices.find((index) => index > cursor) ?? focusSession.frames.length - 1
    const entryIndex = focusSession.frames.findIndex((frame) => frame.phase === 'OPEN')
    const exitIndex = focusSession.frames.findIndex((frame) => frame.phase === 'CLOSED')
    const milestoneIndex = (kind: ReplayFocusFrame['markers'][number]['kind']) => {
      const milestone = focusSession.milestones.find((item) => item.kind === kind)
      return milestone ? focusSession.frames.findIndex((frame) => frame.ts_ms >= milestone.ts_ms) : -1
    }
    const tp1Index = milestoneIndex('TP1_HIT')
    const tp2Index = milestoneIndex('TP2_HIT')
    const replayTrade: HistoryRow | null = trade ? { ...trade, take_profit: focusSession.levels.take_profit_1, take_profit_1: focusSession.levels.take_profit_1, take_profit_2: focusSession.levels.take_profit_2 } : null
    const verificationLabel = focusSession.reconciliation.matched === null
      ? focusSession.reconciliation.applicable ? '차트 준비 완료 · 전체 전략 검증 미실행' : '샘플 UI 검수'
      : focusSession.reconciliation.matched ? '전체 전략 검증 일치' : '전체 전략 검증 확인 필요'
    const seek = (index: number) => {
      const bounded = Math.max(0, Math.min(index, focusSession.frames.length - 1))
      clockRef.current?.seek(bounded)
      setCursor(bounded)
      setPlaying(false)
    }
    const controls = <><button type="button" className="secondary-button" onClick={() => seek(0)}>처음</button><button type="button" className="secondary-button" disabled={cursor === 0} onClick={() => seek(previousKey)}>이전 핵심</button><button type="button" className="primary-button" onClick={() => {
      if (playing) clockRef.current?.pause(); else clockRef.current?.play()
      setPlaying((value) => !value)
    }}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" disabled={cursor >= focusSession.frames.length - 1} onClick={() => seek(cursor + 1)}>다음 이벤트</button><button type="button" className="secondary-button" disabled={cursor >= focusSession.frames.length - 1} onClick={() => seek(nextKey)}>다음 핵심</button><button type="button" className="secondary-button" onClick={() => seek(focusSession.frames.length - 1)}>끝</button><label>속도<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{focusSession.speeds.map((value) => <option value={value} key={value}>{value}×</option>)}</select></label><label className="focus-replay-range">시간<input type="range" min="0" max={Math.max(0, focusSession.frames.length - 1)} value={cursor} onChange={(event) => seek(Number(event.target.value))} /></label><span>{cursor + 1} / {focusSession.frames.length} · {formatKstTime(currentFocusFrame.ts_ms)} 한국시간</span><div className="focus-jumps"><button type="button" onClick={() => seek(0)}>신호</button><button type="button" disabled={entryIndex < 0} onClick={() => seek(entryIndex)}>진입</button><button type="button" disabled={tp1Index < 0} onClick={() => seek(tp1Index)}>1차 목표</button><button type="button" disabled={tp2Index < 0} onClick={() => seek(tp2Index)}>2차 목표</button><button type="button" disabled={exitIndex < 0} onClick={() => seek(exitIndex)}>실제 종료</button></div><details className="focus-timeline"><summary>핵심 과정</summary><ol>{focusSession.milestones.map((item) => { const index = milestoneIndex(item.kind); return <li key={`${item.kind}:${item.ts_ms}`}><button type="button" disabled={index < 0} onClick={() => seek(index)}>{formatKstTime(item.ts_ms)} · {item.label}</button></li> })}</ol></details><span className="checksum">{verificationLabel}</span></>
    return <section className="replay-focus-page" aria-labelledby="replay-focus-heading"><div className="page-heading compact"><div><p className="section-kicker">거래 다시보기</p><h2 id="replay-focus-heading">{focusSession.symbol} 거래 집중 재생</h2></div><span className={speed === 80 ? 'page-note fast-forward' : 'page-note'}>{speed === 80 ? '빨리감기 · 80배속' : 'PAPER · 저장 이벤트만'}</span></div><PositionFocusWorkspace mode={currentFocusFrame.phase === 'CLOSED' ? 'CLOSED_REVIEW' : 'REPLAY'} position={focusPosition} chart={focusChart} overlay={focusOverlay} history={replayTrade && currentFocusFrame.phase === 'CLOSED' ? [replayTrade] : []} replayMilestones={currentFocusFrame.markers} controls={controls} /></section>
  }

  return (
    <section aria-labelledby="replay-heading">
      <div className="page-heading">
        <div><p className="section-kicker">과거 재생</p><h2 id="replay-heading">과거 데이터 다시 보기</h2><p className="heading-help">저장한 시장 데이터를 같은 조건으로 다시 돌려 판단 과정을 확인합니다.</p></div>
        <span className="page-note">{running ? '전략 검증 작동 중' : result ? `검증 완료 · ${replayResultScopeSymbol(result) ?? '전체 종목'}` : timeline ? '저장 데이터 확인됨' : '저장 데이터 확인 전'}</span>
      </div>
      {error ? <p className="control-error" role="alert">{error}</p> : null}
      <section className="panel replay-runbar">
        <label>저장 기록<select value={selectedRun} disabled={loading || timelineLoading || runs.length === 0} onChange={(event) => void changeRun(event.target.value)}>{runs.map((run) => <option value={run.run_id} key={run.run_id}>{new Date(run.started_ts_ms).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} · {run.market_event_count === null ? '시장 데이터 저장됨' : `이벤트 ${run.market_event_count.toLocaleString()}건`}</option>)}</select></label>
        <label>종목<select value={selectedSymbol} disabled={!timeline || previewLoading || timelineLoading} onChange={(event) => void changeSymbol(event.target.value)}>{timeline?.available_symbols.map((item) => <option value={item.symbol} key={item.symbol}>{item.symbol} · {item.event_count === null ? '저장 데이터 있음' : `${item.event_count.toLocaleString()}건`}</option>)}</select></label>
        <button type="button" className="primary-button" disabled={!selectedRun || !selectedSymbol || timelineLoading || running} onClick={() => void loadSelectedTimeline()}>{timelineLoading ? '정밀 이벤트 불러오는 중' : timeline?.preview_only ? '정밀 이벤트 불러오기' : '정밀 이벤트 다시 불러오기'}</button>
        <button type="button" className="secondary-button" disabled={!selectedRun || !timeline || timeline.preview_only || running || timelineLoading} onClick={() => void runReplay()}>{running ? '전략 검증 중' : '같은 조건으로 전략 검증'}</button>
      </section>
      {operation && replayOperationActive(operation) ? <section className="panel replay-operation" role="status" aria-live="polite"><div><span className="operation-dot" aria-hidden="true" /><b>{operation.stage_ko}</b><p>{operation.symbol ?? '전체 종목'} · {operation.total_events === null ? '이벤트 수 확인 중' : `고정 입력 ${operation.total_events.toLocaleString()}건`} · 경과 {elapsedLabel(operation.started_ts_ms, operationNow)}</p><small>공개시장 PAPER 관찰은 계속되며 실제 주문과 인증 경로는 0입니다.</small></div><button type="button" className="operation-secondary" disabled={operation.state === 'CANCELLING'} onClick={() => void cancelReplay()}>{operation.state === 'CANCELLING' ? '취소 중' : '전략 검증 취소'}</button></section> : null}
      {loading ? <div className="panel empty-state"><b>저장 기록 목록을 확인하는 중입니다</b></div> : null}
      {!loading && previewLoading ? <div className="panel replay-load-status" role="status"><b>저장된 최근 캔들을 불러오는 중입니다.</b><span>공개시장 관찰과 PAPER 관리는 계속 작동합니다.</span></div> : null}
      {!loading && timelineLoading ? <div className="panel replay-load-status" role="status"><b>검증된 정밀 이벤트를 불러오는 중입니다.</b><span>대용량 저장 기록은 시간이 걸릴 수 있으며 완료 전까지 전략 검증 버튼이 잠깁니다.</span></div> : null}
      {!loading && runs.length === 0 ? <div className="panel empty-state"><b>다시 볼 저장 기록이 없습니다</b><p>공개시장 모의운영에서 시장 데이터를 먼저 기록하세요.</p></div> : null}
      {timeline ? <>
        <p className="replay-preview-note" role="status">{timeline.preview_only ? `빠른 미리보기 · ${timeline.symbol ?? '종목 없음'} 최근 캔들 ${timeline.candles.length.toLocaleString()}개 · 정밀 이벤트는 버튼을 눌러 불러옵니다.` : `정밀 이벤트 ${timeline.events.length.toLocaleString()}개를 불러왔습니다. 재생하거나 같은 조건으로 전략을 검증할 수 있습니다.`}</p>
        <div className="replay-layout">
          <PriceChart chart={replayChart} history={trade ? [trade] : []} replay />
          <aside className="panel replay-controls"><h3>재생 제어</h3><div className="control-row"><button type="button" className="primary-button" onClick={() => setPlaying((value) => !value)} disabled={timeline.events.length === 0}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" onClick={() => setCursor((value) => Math.min(value + 1, timeline.events.length - 1))} disabled={cursor >= timeline.events.length - 1}>다음 이벤트</button></div><label>속도<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{[0.5, 1, 2, 5, 10, 20, 40, 80].map((value) => <option value={value} key={value}>{value}×</option>)}</select></label><label>이벤트 위치<input type="range" min="0" max={Math.max(0, timeline.events.length - 1)} value={cursor} onChange={(event) => { setCursor(Number(event.target.value)); setPlaying(false) }} /></label><dl><div><dt>현재 위치</dt><dd>{timeline.events.length === 0 ? '없음' : `${cursor + 1} / ${timeline.events.length}`}</dd></div><div><dt>저장된 자료</dt><dd>{timeline.total_events === null ? '전체 건수 집계 중' : `${timeline.total_events.toLocaleString()}건`}{timeline.truncated ? ` · 화면 ${timeline.events.length.toLocaleString()}건 우선` : ''}</dd></div><div><dt>전략 평가</dt><dd>{result ? `${result.strategy_evaluation_count}회` : '실행 전'}</dd></div></dl><details className="advanced-details"><summary>호가·이벤트 기술 정보</summary><dl><div><dt>이벤트 종류·시각</dt><dd>{currentEvent?.event_type ?? '대기'}<br />{currentEvent ? `${formatKstTime(currentEvent.venue_ts_ms)} 한국시간` : '—'}</dd></div><div><dt>매수·매도 호가</dt><dd>{currentQuote ? `${currentQuote.bid} / ${currentQuote.ask}` : '호가 자료 대기'}</dd></div><div><dt>호가 차이</dt><dd>{spreadBps === null ? '—' : `${spreadBps.toFixed(3)} bp`}</dd></div></dl></details></aside>
        </div>
        <section className="replay-proof-grid">
          <article className="panel"><span>전략 검증 결과</span><b>{result ? `후보 ${result.candidate_plan_count} · 공동계좌 ${result.main_trade_count} · 전략별 ${result.shadow_trade_count}` : '전략 검증 전'}</b><small>{result ? `실제 주문 ${result.real_orders_enabled ? '위험' : '0'} · 인증 경로 ${result.auth_required ? '위험' : '0'}` : timeline.preview_only ? '빠른 캔들 미리보기만 표시 중' : '정밀 이벤트를 불러왔으며 전략 검증은 아직 실행하지 않음'}</small></article>
        </section>
        {result ? <details className="panel advanced-details replay-diagnostics"><summary>고급 검증 정보 보기</summary><div className="diagnostic-columns"><div><h3>결정 경로</h3><ol>{result.decision_path.slice(-20).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ol></div><div><h3>최종 상태</h3><p>{result.final_state}</p><h3>입력 검증값</h3><p className="checksum">{result.input_checksum ?? '과거 결과 · 입력 전용 검증값 없음'}</p><h3>종단간 검증값</h3><p className="checksum">{result.checksum}</p></div></div></details> : null}
      </> : null}
    </section>
  )
}
