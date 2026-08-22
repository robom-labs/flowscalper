// 비전문가가 실행 상태·관찰 종목·차트·진행 거래를 한눈에 확인하는 홈 화면이다.
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
  const openTrades = data.position ? 1 : 0
  const watchCount = data.status.deep_symbols || data.scanner.length
  const netPnl = data.status.realized_pnl_usdt + data.status.unrealized_pnl_usdt
  const activityLabel = ready
    ? '시작 전'
    : data.paused
      ? '관찰 중 · 새 진입 멈춤'
      : data.status.market_data_state === 'LIVE'
        ? '자동 관찰 중'
        : '시장 연결 확인 중'
  const latency = data.status.processing_lag_p95_ms === null
    ? ready ? '시작 후 측정' : '측정 중'
    : `${data.status.processing_lag_p95_ms.toFixed(0)} ms`
  return (
    <section aria-labelledby="live-heading">
      <div className="page-heading">
        <div>
          <p className="section-kicker">HOME</p>
          <h2 id="live-heading">자동 관찰 홈</h2>
          <p className="heading-help">공개시장 데이터를 보며 모의로만 거래합니다. 실제 돈은 움직이지 않습니다.</p>
        </div>
        {ready ? (
          <div className="control-row">
            <button type="button" className="primary-button" onClick={onStartLive}>자동 관찰 시작</button>
            <button type="button" className="secondary-button" onClick={onStartDemo}>샘플 화면 보기</button>
          </div>
        ) : (
          <button type="button" className={data.paused ? 'primary-button' : 'secondary-button'} onClick={onPauseToggle}>
            {data.paused ? '자동 관찰 계속하기' : '새 진입 잠시 멈추기'}
          </button>
        )}
      </div>
      <section className="metric-strip home-summary" aria-label="자동 관찰 요약">
        <article><span>프로그램 상태</span><b>{activityLabel}</b></article>
        <article><span>진행 중인 거래</span><b>{openTrades}건</b></article>
        <article><span>완료한 거래</span><b>{data.status.trade_count}건</b></article>
        <article><span>현재 순손익</span><b className={netPnl > 0 ? 'positive' : netPnl < 0 ? 'negative' : ''}>{netPnl.toFixed(4)} USDT</b></article>
        <article><span>정밀 관찰 종목</span><b>{watchCount}개</b></article>
      </section>
      <details className="connection-details">
        <summary>연결과 지연 상태 보기</summary>
        <span>시장 데이터 {data.status.market_data_state === 'LIVE' ? '정상 연결' : data.status.market_data_state}</span>
        <span>처리 지연 최근 95% 기준 {latency}</span>
        <span>누적 비용 {(data.status.cumulative_fees_usdt + data.status.cumulative_slippage_usdt).toFixed(4)} USDT</span>
      </details>
      <section className="chart-toolbar" aria-label="차트 선택">
        <label>볼 종목<select value={data.chart.symbol} onChange={(event) => onChartChange(event.target.value, currentInterval)}>{symbols.map((symbol) => <option value={symbol} key={symbol}>{symbol}</option>)}</select></label>
        <label>차트 간격<select value={currentInterval} onChange={(event) => onChartChange(data.chart.symbol, Number(event.target.value))}>{intervals.map(([seconds, label]) => <option value={seconds} key={seconds}>{label}</option>)}</select></label>
        <span>{data.chart.candles.length > 0 ? `시장 캔들 ${data.chart.candles.length}개` : '시장 캔들 준비 중'}</span>
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
