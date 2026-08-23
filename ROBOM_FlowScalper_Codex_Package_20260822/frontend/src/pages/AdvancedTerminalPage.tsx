// 스캐너·전문 차트·PAPER 포지션·로그를 독립 높이의 고급 화면으로 조합한다.
import { useMemo, useState } from 'react'
import { EventLog } from '../components/EventLog'
import { PositionPanel } from '../components/PositionPanel'
import { PriceChart, type ChartOverlay } from '../components/PriceChart'
import { ScannerTable } from '../components/ScannerTable'
import { strategyLabel } from '../strategyPresentation'
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  onClose: () => void
  onChartChange: (symbol: string, intervalSeconds: number) => void
}

const intervals = [
  [1, '1초'], [5, '5초'], [15, '15초'], [30, '30초'], [60, '1분'], [180, '3분'], [300, '5분'], [600, '10분'], [900, '15분'],
] as const

const intervalSeconds = (label: string) => intervals.find(([, item]) => item.replace('초', 's').replace('분', 'm') === label)?.[0] ?? 1

export function AdvancedTerminalPage({ data, onClose, onChartChange }: Props) {
  const symbols = [...new Set([data.chart.symbol, ...data.scanner.map((row) => row.symbol)])]
  const currentInterval = intervalSeconds(data.chart.interval)
  const matchingLeague = useMemo(() => data.league_positions.filter((position) => position.symbol === data.chart.symbol), [data.chart.symbol, data.league_positions])
  const defaultLeague = matchingLeague.find((position) => position.profile === 'BASE')
  const [overlayId, setOverlayId] = useState('SHARED')
  const effectiveOverlayId = overlayId === 'SHARED' || matchingLeague.some((position) => position.account_id === overlayId)
    ? overlayId
    : defaultLeague?.account_id ?? 'SHARED'
  const selectedLeague = matchingLeague.find((position) => position.account_id === effectiveOverlayId)
  const overlay: ChartOverlay | null = selectedLeague ? {
    key: selectedLeague.trade_id,
    label: `${strategyLabel(data.strategies.find((strategy) => strategy.strategy_id === selectedLeague.strategy_id), selectedLeague.strategy_id)} · ${selectedLeague.profile}`,
    symbol: selectedLeague.symbol,
    side: selectedLeague.side,
    signalTime: selectedLeague.signal_time,
    entry: Number(selectedLeague.actual_entry),
    tp1: Number(selectedLeague.TP1),
    tp2: Number(selectedLeague.TP2),
    stop: Number(selectedLeague.current_stop),
  } : data.position ? {
    key: data.position.strategy,
    label: '공동계좌',
    symbol: data.position.symbol,
    side: data.position.side,
    signalTime: data.position.signal_time,
    entry: Number(data.position.actual_entry),
    tp1: Number(data.position.take_profit_1 ?? data.position.take_profit),
    tp2: data.position.take_profit_2 ? Number(data.position.take_profit_2) : null,
    stop: Number(data.position.current_stop ?? data.position.initial_stop),
  } : null
  return (
    <section aria-labelledby="terminal-heading">
      <div className="page-heading"><div><p className="section-kicker">ADVANCED TERMINAL</p><h2 id="terminal-heading">고급 터미널</h2><p className="heading-help">종목·호가·지표와 모의거래 계획을 자세히 확인하는 화면입니다.</p></div><span className="page-note">보조지표는 Strategy League 진입기준을 바꾸지 않습니다.</span></div>
      <section className="chart-toolbar terminal-toolbar" aria-label="고급 차트 선택">
        <label>볼 종목<select value={data.chart.symbol} onChange={(event) => onChartChange(event.target.value, currentInterval)}>{symbols.map((symbol) => <option value={symbol} key={symbol}>{symbol}</option>)}</select></label>
        <label>차트 간격<select value={currentInterval} onChange={(event) => onChartChange(data.chart.symbol, Number(event.target.value))}>{intervals.map(([seconds, label]) => <option value={seconds} key={seconds}>{label}</option>)}</select></label>
        <label>계획선 계좌<select value={effectiveOverlayId} onChange={(event) => setOverlayId(event.target.value)}><option value="SHARED">공동계좌</option>{matchingLeague.map((position) => <option key={position.account_id} value={position.account_id}>{strategyLabel(data.strategies.find((strategy) => strategy.strategy_id === position.strategy_id), position.strategy_id)} · {position.profile}</option>)}</select></label>
        <span>표시 계좌 · {overlay?.label ?? '열린 포지션 없음'}</span>
      </section>
      <div className="terminal-grid">
        <ScannerTable rows={data.scanner} venue={data.status.venue} selectedSymbol={data.chart.symbol} protectedSymbols={data.league_positions.map((position) => position.symbol)} onSelect={(symbol) => onChartChange(symbol, currentInterval)} />
        <PriceChart chart={data.chart} overlay={overlay} history={data.history.filter((row) => row.run_id === data.status.run_id)} />
        <PositionPanel position={data.position} onClose={onClose} />
        <EventLog logs={data.logs} />
      </div>
    </section>
  )
}
