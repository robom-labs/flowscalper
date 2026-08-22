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
}

export function LivePage({ data, onPauseToggle, onClose, onStartLive, onStartDemo }: Props) {
  const ready = data.status.mode === 'READY'
  return (
    <section aria-labelledby="live-heading">
      <div className="page-heading"><div><p className="section-kicker">LIVE WORKSPACE</p><h2 id="live-heading">라이브 PAPER 관찰</h2></div>{ready ? <div className="control-row"><button type="button" className="primary-button" onClick={onStartLive}>실시간 PAPER 시작</button><button type="button" className="secondary-button" onClick={onStartDemo}>오프라인 샘플 보기</button></div> : <button type="button" className={data.paused ? 'primary-button' : 'secondary-button'} onClick={onPauseToggle}>{data.paused ? '페이퍼 진입 재개' : '페이퍼 진입 일시정지'}</button>}</div>
      <section className="metric-strip" aria-label="계좌 요약">
        <article><span>현재 자산</span><b>{data.status.current_equity_usdt.toFixed(2)} USDT</b></article>
        <article><span>누적 순손익</span><b>{data.status.realized_pnl_usdt.toFixed(4)} USDT</b></article>
        <article><span>누적 수수료</span><b>{data.status.cumulative_fees_usdt.toFixed(4)} USDT</b></article>
        <article><span>Drawdown</span><b>{String(data.performance.max_drawdown)} USDT</b></article>
        <article><span>데이터 지연 p95</span><b>{data.status.processing_lag_p95_ms ?? (ready ? '시작 전' : 'fixture')} ms</b></article>
      </section>
      <div className="live-grid">
        <ScannerTable rows={data.scanner} />
        <PriceChart chart={data.chart} />
        <PositionPanel position={data.position} onClose={onClose} />
        <EventLog logs={data.logs} />
      </div>
    </section>
  )
}
