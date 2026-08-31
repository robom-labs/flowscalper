// 전체 공개시장 탐색과 체결된 PAPER 포지션 집중 화면을 한 고정 작업공간으로 조합한다.
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchJson } from '../api/client'
import { PositionFocusWorkspace } from '../components/PositionFocusWorkspace'
import { PositionList } from '../components/PositionList'
import { PriceChart, type ChartOverlay } from '../components/PriceChart'
import { OperationStatusPanel } from '../components/OperationStatusPanel'
import { SideDrawer } from '../components/SideDrawer'
import { costProfileLabel, formatUsdt, paperAccountLabel, sideLabel } from '../format'
import { strategyLabel } from '../strategyPresentation'
import type { ChartData, ControlOperation, DashboardData, FocusPosition, MarketCatalog, MarketCatalogRow } from '../types'

type Props = {
  data: DashboardData
  onChartChange: (symbol: string, intervalSeconds: number) => void
  onStartLive: () => void
  onStartDemo: () => void
  busy: boolean
  operation: ControlOperation | null
  onCancel: () => void
  onRetry: () => void
}

const intervalFromLabel = (data: DashboardData) => data.timeframes.find((row) => row.label === data.chart.interval)?.interval_seconds ?? 180
const marketPreferenceKey = 'robom.market.workspace.v1'
const focusPreferenceKey = 'robom.position.focus.v1'

type FocusPreference = { autoFocusOnFill: boolean; focusLocked: boolean; defaultProfile: 'BASE' | 'STRESS' }

function loadFocusPreference(): FocusPreference {
  const fallback: FocusPreference = { autoFocusOnFill: true, focusLocked: false, defaultProfile: 'BASE' }
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(focusPreferenceKey) ?? '{}') as Partial<FocusPreference>
    return {
      autoFocusOnFill: parsed.autoFocusOnFill !== false,
      focusLocked: parsed.focusLocked === true,
      defaultProfile: parsed.defaultProfile === 'STRESS' ? 'STRESS' : 'BASE',
    }
  } catch { return fallback }
}

function preferredFocus(rows: FocusPosition[], profile: 'BASE' | 'STRESS') {
  return [...rows].sort((left, right) => Number(left.profile !== profile) - Number(right.profile !== profile) || (left.opened_ts_ms ?? left.signal_ts_ms) - (right.opened_ts_ms ?? right.signal_ts_ms) || left.strategy_id.localeCompare(right.strategy_id) || left.symbol.localeCompare(right.symbol))[0] ?? null
}

function positionOverlay(position: FocusPosition, strategies: DashboardData['strategies']): ChartOverlay {
  return {
    key: position.focus_key,
    label: `${strategyLabel(strategies.find((strategy) => strategy.strategy_id === position.strategy), position.strategy)} · ${costProfileLabel(position.profile)} · ${paperAccountLabel(position.account_id)}`,
    symbol: position.symbol,
    side: position.side,
    signalTime: position.signal_time,
    entry: Number(position.actual_entry),
    tp1: Number(position.take_profit_1),
    tp2: position.take_profit_2 ? Number(position.take_profit_2) : null,
    stop: Number(position.current_stop),
    initialStop: Number(position.initial_stop),
    currentStop: Number(position.current_stop),
  }
}

function loadMarketPreference(data: DashboardData): { source: 'BINANCE_USDM' | 'UPBIT_KRW'; symbol: string; interval: number } {
  const fallback = { source: 'BINANCE_USDM' as const, symbol: data.chart.symbol, interval: intervalFromLabel(data) }
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(marketPreferenceKey) ?? '') as { source?: string; symbol?: string; interval?: number }
    if ((parsed.source === 'BINANCE_USDM' || parsed.source === 'UPBIT_KRW') && parsed.symbol && data.timeframes.some((row) => row.interval_seconds === parsed.interval)) {
      return { source: parsed.source, symbol: parsed.symbol, interval: parsed.interval as number }
    }
  } catch { /* 저장 환경이 없거나 값이 손상되면 현재 서버 선택으로 시작한다. */ }
  return fallback
}

function fallbackCatalog(data: DashboardData): MarketCatalogRow[] {
  return data.scanner.map((row) => ({
    venue: 'BINANCE_USDM', symbol: row.symbol, display_symbol: row.symbol.replace('USDT', '/USDT'),
    base_asset: row.symbol.replace('USDT', ''), quote_asset: 'USDT', market_role: 'PAPER_EXECUTION',
    last: 0, bid: 0, ask: 0, change_percent: 0, quote_volume_24h: 0, trade_count_24h: 0,
    status: row.status,
  }))
}

function formatCompact(value: number) {
  return new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

const defaultMarketRowLimit = 10

function MarketRail({ rows, preferredSymbols, selected, onSelect, onClose }: { rows: MarketCatalogRow[]; preferredSymbols: string[]; selected: string; onSelect: (row: MarketCatalogRow) => void; onClose?: () => void }) {
  const [query, setQuery] = useState('')
  const [venue, setVenue] = useState<'BINANCE_USDM' | 'UPBIT_KRW'>('BINANCE_USDM')
  const [scrollTop, setScrollTop] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const venueRows = rows.filter((row) => row.venue === venue)
  const preferredRows: MarketCatalogRow[] = []
  const preferredKeys = new Set<string>()
  for (const symbol of preferredSymbols) {
    const row = venueRows.find((candidate) => candidate.symbol === symbol)
    if (!row || preferredKeys.has(row.symbol)) continue
    preferredRows.push(row)
    preferredKeys.add(row.symbol)
  }
  const defaultRows = [...preferredRows, ...venueRows.filter((row) => !preferredKeys.has(row.symbol))].slice(0, defaultMarketRowLimit)
  const normalizedQuery = query.trim().toLowerCase()
  const searchedRows = normalizedQuery
    ? venueRows.filter((row) => `${row.symbol} ${row.display_symbol} ${row.korean_name ?? ''} ${row.english_name ?? ''}`.toLowerCase().includes(normalizedQuery))
    : venueRows
  const filtered = normalizedQuery || showAll ? searchedRows : defaultRows
  const start = Math.max(0, Math.floor(scrollTop / 52) - 5)
  const visible = filtered.slice(start, start + 40)
  return <aside className="market-rail" aria-label="전체 종목 탐색">
    <div className="market-rail-head"><strong>종목</strong><span>{normalizedQuery || showAll ? `${filtered.length}개` : `${filtered.length}/${venueRows.length}개`}</span>{!normalizedQuery && venueRows.length > defaultRows.length ? <button type="button" className="market-rail-close" aria-expanded={showAll} onClick={() => { setShowAll((current) => !current); setScrollTop(0) }}>{showAll ? '상위 10개' : '전체보기'}</button> : null}{onClose ? <button type="button" className="market-rail-close" onClick={onClose}>닫기</button> : null}</div>
    <div className="venue-tabs" role="group" aria-label="시장 선택">
      <button type="button" aria-pressed={venue === 'BINANCE_USDM'} onClick={() => { setVenue('BINANCE_USDM'); setShowAll(false); setScrollTop(0) }}>USDT 선물</button>
      <button type="button" aria-pressed={venue === 'UPBIT_KRW'} onClick={() => { setVenue('UPBIT_KRW'); setShowAll(false); setScrollTop(0) }}>원화 참고</button>
    </div>
    <input className="market-search" aria-label="종목 검색" placeholder="BTC, 비트코인 검색" value={query} onChange={(event) => { setQuery(event.target.value); setScrollTop(0) }} />
    <div className="market-list" onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
      <div className="market-list-virtual" style={{ height: `${filtered.length * 52}px` }}><div className="market-window" style={{ transform: `translateY(${start * 52}px)` }}>{visible.map((row) => <button type="button" key={`${row.venue}:${row.symbol}`} className={selected === row.symbol ? 'market-row selected' : 'market-row'} onClick={() => onSelect(row)}>
        <span><b>{row.korean_name || row.display_symbol}</b><small>{row.display_symbol} · {row.market_role === 'OBSERVATION_ONLY' ? '관찰 전용' : 'PAPER 가능'}</small></span>
        <span><b>{row.last ? row.last.toLocaleString('ko-KR', { maximumFractionDigits: 6 }) : '관찰 중'}</b><small>{row.quote_volume_24h ? `거래대금 ${formatCompact(row.quote_volume_24h)}` : row.status}</small></span>
      </button>)}</div></div>
      {!filtered.length ? <p className="market-empty">조건에 맞는 종목이 없습니다.</p> : null}
    </div>
  </aside>
}

export function MarketPage({ data, onChartChange, onStartLive, onStartDemo, busy, operation, onCancel, onRetry }: Props) {
  const [initialPreference] = useState(() => loadMarketPreference(data))
  const [initialFocusPreference] = useState(loadFocusPreference)
  const [initialFocused] = useState(() => initialFocusPreference.autoFocusOnFill ? preferredFocus(data.focus_positions, initialFocusPreference.defaultProfile) : null)
  const [catalog, setCatalog] = useState<MarketCatalogRow[]>(() => fallbackCatalog(data))
  const [catalogError, setCatalogError] = useState('')
  const [historical, setHistorical] = useState<ChartData['candles']>([])
  const [selectedMarket, setSelectedMarket] = useState<{ source: 'BINANCE_USDM' | 'UPBIT_KRW'; symbol: string }>(initialFocused ? { source: 'BINANCE_USDM', symbol: initialFocused.symbol } : { source: initialPreference.source, symbol: initialPreference.symbol })
  const [selectedInterval, setSelectedInterval] = useState<number>(initialPreference.interval)
  const [focusKey, setFocusKey] = useState<string | null>(initialFocused?.focus_key ?? null)
  const [focusLocked, setFocusLocked] = useState(initialFocusPreference.focusLocked)
  const [focusNotice, setFocusNotice] = useState('')
  const [closedReview, setClosedReview] = useState<FocusPosition | null>(null)
  const [marketDrawer, setMarketDrawer] = useState(false)
  const [positionsDrawer, setPositionsDrawer] = useState(false)
  const knownTrades = useRef(new Set(data.focus_positions.map((position) => position.trade_id)))
  const lastFocus = useRef<FocusPosition | null>(initialFocused)
  const lastRunId = useRef(data.status.run_id)
  const interval = selectedInterval
  const explorerEnabled = data.status.mode !== 'DEMO_FIXTURE' || data.status.health_flags.includes('E2E_MARKET_EXPLORER')
  const fixture = data.status.mode === 'DEMO_FIXTURE'

  useEffect(() => {
    try { globalThis.localStorage?.setItem(marketPreferenceKey, JSON.stringify({ ...selectedMarket, interval })) } catch { /* 저장이 막힌 브라우저에서는 현재 세션 선택만 유지한다. */ }
  }, [interval, selectedMarket])

  useEffect(() => {
    try { globalThis.localStorage?.setItem(focusPreferenceKey, JSON.stringify({ autoFocusOnFill: initialFocusPreference.autoFocusOnFill, focusLocked, defaultProfile: initialFocusPreference.defaultProfile })) } catch { /* 저장 불가 환경에서는 현재 세션 설정만 유지한다. */ }
  }, [focusLocked, initialFocusPreference])

  useEffect(() => {
    if (!explorerEnabled) return
    const controller = new AbortController()
    void fetchJson<MarketCatalog>('/api/markets/catalog', { signal: controller.signal }, 12_000)
      .then((result) => {
        if (Array.isArray(result.rows)) setCatalog(result.rows)
        setCatalogError('')
      })
      .catch(() => setCatalogError('전체 목록 연결을 기다리는 중입니다. 정밀분석 종목은 계속 볼 수 있습니다.'))
    return () => controller.abort()
  }, [explorerEnabled])

  useEffect(() => {
    if (!explorerEnabled) return
    const controller = new AbortController()
    void fetchJson<{ candles: ChartData['candles'] }>(`/api/markets/candles?source=${selectedMarket.source}&symbol=${encodeURIComponent(selectedMarket.symbol)}&interval_seconds=${interval}&limit=200`, { signal: controller.signal }, 12_000)
      .then((result) => setHistorical(Array.isArray(result.candles) ? result.candles : []))
      .catch(() => setHistorical([]))
    return () => controller.abort()
  }, [explorerEnabled, interval, selectedMarket])

  useEffect(() => {
    if (lastRunId.current === data.status.run_id) return
    lastRunId.current = data.status.run_id
    setFocusNotice('')
    setFocusKey(null)
    setClosedReview(null)
    lastFocus.current = null
  }, [data.status.run_id])

  useEffect(() => {
    if (data.status.mode === 'READY') {
      knownTrades.current = new Set(data.focus_positions.map((position) => position.trade_id))
      lastFocus.current = null
      const clearTimer = window.setTimeout(() => {
        setFocusNotice('')
        setFocusKey(null)
        setClosedReview(null)
      }, 0)
      return () => window.clearTimeout(clearTimer)
    }
    const arrivals = data.focus_positions.filter((position) => !knownTrades.current.has(position.trade_id) && position.auto_focus_eligible)
    data.focus_positions.forEach((position) => knownTrades.current.add(position.trade_id))
    const newPosition = preferredFocus(arrivals, initialFocusPreference.defaultProfile)
    if (newPosition && initialFocusPreference.autoFocusOnFill && !focusLocked) {
      setFocusKey(newPosition.focus_key)
      setClosedReview(null)
      setSelectedMarket({ source: 'BINANCE_USDM', symbol: newPosition.symbol })
      onChartChange(newPosition.symbol, interval)
      setFocusNotice(arrivals.length > 1 ? `새 PAPER 진입 ${arrivals.length}건 · 기본 비용 우선 표시` : `새 PAPER 진입 · ${newPosition.symbol} · ${newPosition.strategy_display_name_ko} · ${costProfileLabel(newPosition.profile)} · ${paperAccountLabel(newPosition.account_id)}`)
    } else if (newPosition) {
      setFocusNotice(arrivals.length > 1 ? `새 PAPER 진입 ${arrivals.length}건 · 포지션 선택에서 확인하세요.` : `새 PAPER 진입 · ${newPosition.symbol} · 거래 보기를 선택하세요.`)
    }
  }, [data.focus_positions, data.status.mode, focusLocked, initialFocusPreference, interval, onChartChange])

  const focus = data.focus_positions.find((position) => position.focus_key === focusKey) ?? null
  useEffect(() => {
    if (focus) {
      lastFocus.current = focus
      if (!closedReview) return
      const clearTimer = window.setTimeout(() => setClosedReview(null), 0)
      return () => window.clearTimeout(clearTimer)
    }
    if (!focusKey || !lastFocus.current || closedReview) return
    const completed = { ...lastFocus.current, stage: 'CLOSED', stage_ko: '거래 종료', management_reason: '최종 PAPER 결과 확인', management_reason_ko: '최종 PAPER 결과 확인', remaining_quantity: '0' }
    const reviewTimer = window.setTimeout(() => {
      setClosedReview(completed)
      setFocusNotice(`PAPER 거래 종료 · ${completed.symbol} · ${completed.strategy_display_name_ko} · ${costProfileLabel(completed.profile)} · ${paperAccountLabel(completed.account_id)} · 기록에서 확인`)
    }, 0)
    return () => window.clearTimeout(reviewTimer)
  }, [closedReview, focus, focusKey])
  useEffect(() => {
    if (!closedReview || focusLocked) return
    const timer = window.setTimeout(() => {
      setClosedReview(null)
      setFocusKey(null)
      setFocusNotice((current) => current.startsWith('PAPER 거래 종료') ? '' : current)
    }, 15_000)
    return () => window.clearTimeout(timer)
  }, [closedReview, focusLocked])
  const displayedFocus = focus ?? closedReview
  const chart = useMemo<ChartData>(() => {
    const canMergeRuntime = selectedMarket.source === 'BINANCE_USDM'
      && data.chart.symbol === selectedMarket.symbol
      && (!explorerEnabled || data.status.market_data_state === 'LIVE')
    const runtimeCandles = canMergeRuntime ? data.chart.candles : []
    const merged = new Map<number, ChartData['candles'][number]>()
    for (const candle of [...(explorerEnabled ? historical : []), ...runtimeCandles]) merged.set(candle.open_ts_ms, candle)
    return { ...data.chart, symbol: selectedMarket.symbol, interval: data.timeframes.find((row) => row.interval_seconds === interval)?.label ?? '3m', points: canMergeRuntime ? data.chart.points : [], candles: [...merged.values()].sort((left, right) => left.open_ts_ms - right.open_ts_ms).slice(-500), lines: canMergeRuntime ? data.chart.lines : { entry: null, take_profit: null, take_profit_2: null, stop: null } }
  }, [data.chart, data.status.market_data_state, data.timeframes, explorerEnabled, historical, interval, selectedMarket])
  const overlay = displayedFocus ? positionOverlay(displayedFocus, data.strategies) : null
  const selectedSymbolPositions = data.focus_positions.filter((position) => position.symbol === selectedMarket.symbol)
  const marketPosition = preferredFocus(selectedSymbolPositions, initialFocusPreference.defaultProfile)
  const marketOverlay = marketPosition ? positionOverlay(marketPosition, data.strategies) : null
  const openPosition = (position: FocusPosition) => {
    setFocusKey(position.focus_key)
    setClosedReview(null)
    setSelectedMarket({ source: 'BINANCE_USDM', symbol: position.symbol })
    onChartChange(position.symbol, interval)
  }
  const selectMarket = (row: MarketCatalogRow) => {
    setSelectedMarket({ source: row.venue, symbol: row.symbol })
    setHistorical([])
    setCatalogError(row.market_role === 'OBSERVATION_ONLY' ? '관찰 전용 · KRW 현물 공개시세 · 전략과 PAPER 체결에는 사용하지 않습니다.' : '')
    void fetchJson('/api/markets/select', { method: 'POST', body: JSON.stringify({ source: row.venue, symbol: row.symbol, interval_seconds: interval, pin_for_analysis: row.market_role === 'PAPER_EXECUTION' }) }).catch(() => undefined)
    if (row.market_role === 'PAPER_EXECUTION') onChartChange(row.symbol, interval)
  }
  const activeRows = !explorerEnabled ? fallbackCatalog(data) : catalog.length ? catalog : fallbackCatalog(data)
  const enabledStrategies = data.strategies.filter((strategy) => strategy.mode !== 'OFF' && (strategy.long_enabled || strategy.short_enabled))
  const noTradeReason = enabledStrategies.find((strategy) => strategy.reason_ko)?.reason_ko
    || (data.operation_status.paper_entry_active ? '현재 전략의 세부 진입 조건을 기다리고 있습니다.' : data.operation_status.detail_ko)
  const operationNeedsDetail = data.operation_status.state !== 'RUNNING'
  return <section className={displayedFocus ? 'market-workspace focus-mode' : 'market-workspace'} aria-labelledby="market-heading">
    {fixture ? <p className="mode-truth-banner" role="status">샘플 PAPER 데이터 · LIVE 아님</p> : null}
    <section className="market-summary" aria-label="시장 요약">
      <article><span>공개시장 연결</span><b>{data.status.market_data_state === 'LIVE' ? '연결됨' : data.status.mode === 'DEMO_FIXTURE' ? '샘플' : '대기'}</b></article>
      <article><span>현재자산</span><b>{formatUsdt(data.status.current_equity_usdt, { equity: true })}</b></article>
      <article><span>오늘 순손익</span><b className={Number(data.status.realized_pnl_usdt) >= 0 ? 'positive' : 'negative'}>{formatUsdt(data.status.realized_pnl_usdt, { signed: true })}</b></article>
      <article><span>진행 PAPER</span><b>{data.focus_positions.length}건</b></article>
      <article><span>켜진 전략</span><b>{enabledStrategies.length}개</b></article>
    </section>
    <header className={data.status.mode === 'READY' ? 'market-toolbar ready-mode' : 'market-toolbar'}>
      <div><h2 id="market-heading">{displayedFocus ? `${displayedFocus.symbol} 포지션 집중` : `${selectedMarket.symbol} 시장`}</h2><span>{displayedFocus ? `${sideLabel(displayedFocus.side)} · ${displayedFocus.strategy_display_name_ko} · ${costProfileLabel(displayedFocus.profile)} · ${paperAccountLabel(displayedFocus.account_id)} · PAPER` : selectedMarket.source === 'UPBIT_KRW' ? '관찰 전용 · KRW 현물' : data.status.market_data_state === 'LIVE' ? '실시간 공개시장 · PAPER만' : data.status.mode === 'DEMO_FIXTURE' ? '연습용 샘플 · 실시간 아님' : '공개시장 연결 대기'}</span></div>
      <label>시간<select aria-label="차트 시간" value={interval} onChange={(event) => { const next = Number(event.target.value); setSelectedInterval(next); if (selectedMarket.source === 'BINANCE_USDM') onChartChange(selectedMarket.symbol, next) }}>{data.timeframes.map((row) => <option key={row.interval_seconds} value={row.interval_seconds}>{row.label_ko}</option>)}</select></label>
      {data.focus_positions.length ? <label>포지션<select aria-label="집중 포지션" value={focusKey ?? ''} onChange={(event) => { const position = data.focus_positions.find((row) => row.focus_key === event.target.value); if (position) openPosition(position); else setFocusKey(null) }}><option value="">시장 보기</option>{data.focus_positions.map((position) => <option value={position.focus_key} key={position.focus_key}>{position.symbol} · {position.side === 'LONG' ? '상승' : '하락'} · {position.strategy_display_name_ko} · {costProfileLabel(position.profile)} · {paperAccountLabel(position.account_id)}</option>)}</select></label> : null}
      {displayedFocus ? <><button type="button" className={focusLocked ? 'workspace-button selected' : 'workspace-button'} aria-pressed={focusLocked} onClick={() => setFocusLocked((value) => !value)}>{focusLocked ? '현재 거래 고정됨' : '현재 거래 고정'}</button><button type="button" className="workspace-button" onClick={() => { setFocusKey(null); setClosedReview(null); setFocusNotice('') }}>시장으로</button></> : null}
      <button type="button" className="workspace-button" onClick={() => setPositionsDrawer(true)}>진행 포지션</button>
      <button type="button" className="workspace-button market-drawer-button" onClick={() => setMarketDrawer(true)}>종목</button>
    </header>
    {focusNotice ? <p className="market-notice focus-toast" role="status">{focusNotice}<button type="button" onClick={() => setFocusNotice('')}>닫기</button></p> : catalogError ? <p className="market-notice" role="status">{catalogError}</p> : null}
    {operationNeedsDetail ? <OperationStatusPanel data={data} busy={busy} operation={operation} onStartLive={onStartLive} onStartDemo={onStartDemo} onCancel={onCancel} onRetry={onRetry} /> : <p className="operation-compact" role="status" aria-label="프로그램 작동 상태"><strong>{data.operation_status.title_ko}</strong><span>{data.operation_status.detail_ko}</span></p>}
    {!data.focus_positions.length ? <details className="market-no-trade"><summary>왜 거래가 없나요?</summary><p>{noTradeReason}</p></details> : null}
    {data.focus_positions.length ? <section className="open-position-strip" aria-label="현재 PAPER 진입 목록"><strong>진행 중 {data.focus_positions.length}건</strong><div>{data.focus_positions.map((position) => <button type="button" className={position.side === 'LONG' ? 'long' : 'short'} aria-pressed={focusKey === position.focus_key} key={position.focus_key} onClick={() => openPosition(position)}><b>{position.symbol} · {position.side === 'LONG' ? '상승' : '하락'}</b><span>{position.strategy_display_name_ko} · {costProfileLabel(position.profile)} · {paperAccountLabel(position.account_id)} · {position.stage_ko}</span><small>{formatUsdt(position.net_pnl_usdt, { signed: true })}</small></button>)}</div></section> : null}
    {displayedFocus && overlay ? <PositionFocusWorkspace mode={focus ? 'LIVE' : 'CLOSED_REVIEW'} position={displayedFocus} chart={chart} overlay={overlay} history={data.history.filter((row) => row.run_id === data.status.run_id)} /> : <div className="market-grid"><MarketRail rows={activeRows} preferredSymbols={data.scanner.filter((row) => row.depth === 'DEEP').map((row) => row.symbol)} selected={selectedMarket.symbol} onSelect={selectMarket} /><PriceChart chart={chart} overlay={marketOverlay} activePositionCount={selectedSymbolPositions.length} history={selectedMarket.source === 'BINANCE_USDM' && data.status.market_data_state === 'LIVE' ? data.history.filter((row) => row.run_id === data.status.run_id) : []} compact /></div>}
    {marketDrawer ? <div className="market-drawer-layer" role="dialog" aria-label="종목 목록"><button type="button" className="drawer-backdrop" aria-label="종목 목록 바깥 닫기" onClick={() => setMarketDrawer(false)} /><MarketRail rows={activeRows} preferredSymbols={data.scanner.filter((row) => row.depth === 'DEEP').map((row) => row.symbol)} selected={selectedMarket.symbol} onSelect={(row) => { selectMarket(row); setMarketDrawer(false) }} onClose={() => setMarketDrawer(false)} /></div> : null}
    <SideDrawer title="진행 중인 PAPER 거래" open={positionsDrawer} onClose={() => setPositionsDrawer(false)} label="진행 포지션"><PositionList positions={data.league_positions} strategies={data.strategies} compact /></SideDrawer>
  </section>
}
