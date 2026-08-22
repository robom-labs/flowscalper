// 스캐너·차트·현재 거래·로그를 한 화면에서 확인하는 라이브 작업공간이다.
import { EventLog } from '../components/EventLog'
import { PositionPanel } from '../components/PositionPanel'
import { PriceChart } from '../components/PriceChart'
import { ScannerTable } from '../components/ScannerTable'
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  onPauseToggle: () => void
  onClose: () => void
  onStartLive: () => void
  onStartDemo: () => void
  onChartChange: (symbol: string, intervalSeconds: number) => void
}

const intervals = [
  [1, '1초'], [5, '5초'], [15, '15초'], [30, '30초'], [60, '1분'], [180, '3분'], [300, '5분'], [600, '10분'], [900, '15분'],
] as const

const intervalSeconds = (label: string) => intervals.find(([, item]) => item.replace('초', 's').replace('분', 'm') === label)?.[0] ?? 1

export function LivePage({ data, onPauseToggle, onClose, onStartLive, onStartDemo, onChartChange }: Props) {
  const ready = data.status.mode === 'READY'
  const symbols = [...new Set([data.chart.symbol, ...data.scanner.map((row) => row.symbol)])]
  const currentInterval = intervalSeconds(data.chart.interval)
  const latency = data.status.processing_lag_p95_ms === null
    ? ready ? '시작 전' : '측정 중'
    : `${data.status.processing_lag_p95_ms.toFixed(0)} ms`
  return (
    <section aria-labelledby="live-heading">
      <div className="page-heading"><div><p className="section-kicker">LIVE WORKSPACE</p><h2 id="live-heading">{data.status.mode === 'DEMO_FIXTURE' ? '오프라인 DEMO 관찰' : '라이브 PAPER 관찰'}</h2><p className="heading-help">실제 주문 없이 공개시장 신호와 PAPER 체결만 관찰합니다.</p></div>{ready ? <div className="control-row"><button type="button" className="primary-button" onClick={onStartLive}>실시간 PAPER 시작</button><button type="button" className="secondary-button" onClick={onStartDemo}>오프라인 샘플 보기</button></div> : <button type="button" className={data.paused ? 'primary-button' : 'secondary-button'} onClick={onPauseToggle}>{data.paused ? '페이퍼 진입 재개' : '페이퍼 진입 일시정지'}</button>}</div>
      <section className="metric-strip" aria-label="계좌 요약">
        <article><span>현재 자산</span><b>{data.status.current_equity_usdt.toFixed(2)} USDT</b></article>
        <article><span>현재 포함 순손익</span><b>{(data.status.realized_pnl_usdt + data.status.unrealized_pnl_usdt).toFixed(4)} USDT</b></article>
        <article><span>누적 수수료</span><b>{data.status.cumulative_fees_usdt.toFixed(4)} USDT</b></article>
        <article><span>Drawdown</span><b>{String(data.performance.max_drawdown)} USDT</b></article>
        <article><span>데이터 지연 p95</span><b>{latency}</b></article>
      </section>
      <section className="chart-toolbar" aria-label="차트 선택">
        <label>차트 종목<select value={data.chart.symbol} onChange={(event) => onChartChange(event.target.value, currentInterval)}>{symbols.map((symbol) => <option value={symbol} key={symbol}>{symbol}</option>)}</select></label>
        <label>시간구간<select value={currentInterval} onChange={(event) => onChartChange(data.chart.symbol, Number(event.target.value))}>{intervals.map(([seconds, label]) => <option value={seconds} key={seconds}>{label}</option>)}</select></label>
        <span>{data.chart.candles.length > 0 ? `실제 체결 캔들 ${data.chart.candles.length}개` : '실제 체결 캔들 준비 중'}</span>
      </section>
      <div className="live-grid">
        <ScannerTable rows={data.scanner} selectedSymbol={data.chart.symbol} onSelect={(symbol) => onChartChange(symbol, currentInterval)} />
        <PriceChart chart={data.chart} position={data.position} history={data.history} />
        <PositionPanel position={data.position} onClose={onClose} />
        <EventLog logs={data.logs} />
      </div>
    </section>
  )
}
