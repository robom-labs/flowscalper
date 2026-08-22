// 표본수와 비용을 숨기지 않는 PAPER 성과 분석 화면이다.
import type { DashboardData } from '../types'

type Props = { performance: DashboardData['performance'] }

export function PerformancePage({ performance }: Props) {
  const metrics = [
    ['표본수', performance.sample_size], ['총손익', `${performance.gross_pnl} USDT`], ['수수료', `${performance.fees} USDT`], ['슬리피지', `${performance.slippage} USDT`], ['순손익', `${performance.net_pnl} USDT`], ['최대 Drawdown', `${performance.max_drawdown}%`],
  ]
  return (
    <section aria-labelledby="performance-heading">
      <div className="page-heading"><div><p className="section-kicker">RESEARCH HONESTY</p><h2 id="performance-heading">성과분석</h2></div><span className="calibrating">{performance.calibration}</span></div>
      <section className="analytics-grid">{metrics.map(([label, value]) => <article className="panel analytics-card" key={String(label)}><span>{label}</span><b>{value}</b></article>)}</section>
      <div className="analytics-panels"><section className="panel"><h3>BASE vs STRESS</h3><div className="comparison"><div><span>BASE</span><strong>{performance.base_equity} USDT</strong><i style={{ width: '86%' }} /></div><div><span>STRESS</span><strong>{performance.stress_equity} USDT</strong><i style={{ width: '62%' }} /></div></div></section><section className="panel"><h3>통계적 주의</h3><p>{performance.win_rate}</p><p>짧은 표본을 연환산하지 않으며, fixture 결과를 수익성 근거로 사용하지 않습니다.</p></section></div>
    </section>
  )
}

