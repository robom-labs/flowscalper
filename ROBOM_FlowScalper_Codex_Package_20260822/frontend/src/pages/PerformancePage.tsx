// 표본수·비용·기대값·Profit Factor·낙폭을 전략과 shadow 계좌별로 숨김없이 비교한다.
import { PerformanceCurve } from '../components/PerformanceCurve'
import type { DashboardData, HistoryRow, ShadowAccount, StrategyRow } from '../types'

type Props = {
  performance: DashboardData['performance']
  strategies: StrategyRow[]
  shadowAccounts: ShadowAccount[]
  history: HistoryRow[]
}

function printable(value: string | number | null, suffix = '') {
  return value === null ? '표본 없음' : `${value}${suffix}`
}

export function PerformancePage({ performance, strategies, shadowAccounts, history }: Props) {
  const metrics = [
    ['완료 거래', `${performance.sample_size}건`],
    ['총손익', `${performance.gross_pnl} USDT`],
    ['수수료', `${performance.fees} USDT`],
    ['슬리피지', `${performance.slippage} USDT`],
    ['순손익', `${performance.net_pnl} USDT`],
    ['최대 낙폭', `${performance.max_drawdown} USDT`],
  ]
  return (
    <section aria-labelledby="performance-heading">
      <div className="page-heading"><div><p className="section-kicker">RESULTS</p><h2 id="performance-heading">모의매매 결과</h2><p className="heading-help">완료 거래, 비용, 순손익을 먼저 보여주고 전문 통계는 아래에서 따로 확인합니다.</p></div><span className="calibrating">{Number(performance.sample_size) < 30 ? '데이터 모으는 중' : '분석 가능'}</span></div>
      <section className="analytics-grid">{metrics.map(([label, value]) => <article className="panel analytics-card" key={String(label)}><span>{label}</span><b>{value}</b></article>)}</section>
      <section className="panel performance-curve-panel"><div className="panel-title"><div><p className="section-kicker">MAIN PAPER</p><h3>자산곡선 · 낙폭</h3></div><span>시작자산 1,000 USDT</span></div><PerformanceCurve history={history.filter((row) => row.profile === 'BASE')} /></section>
      <details className="panel advanced-details strategy-performance-panel"><summary>전략별 전문 통계 보기</summary><div className="panel-title"><div><p className="section-kicker">STRATEGY BREAKDOWN</p><h3>전략별 기본·비용증가 비교</h3></div><span>판단은 참고용이며 자동 중지하지 않음</span></div><div className="table-scroll"><table className="performance-table"><thead><tr><th>전략</th><th>비용 프로필</th><th>표본 / 승률</th><th>기대값</th><th>Profit Factor</th><th>비용</th><th>최대 낙폭</th><th>보유 중앙 / p90</th><th>표본상태</th><th>권고</th></tr></thead><tbody>{strategies.flatMap((strategy) => (['BASE', 'STRESS'] as const).map((profile) => {
        const report = strategy.performance[profile]
        return <tr key={`${strategy.strategy_id}-${profile}`}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko}</small></td><td>{profile}</td><td>{report.sample_size}건<br />{report.win_rate === null ? '승률 없음' : `${(Number(report.win_rate) * 100).toFixed(1)}%`}</td><td>{printable(report.expectancy_usdt, ' USDT')}<small>{printable(report.expectancy_r, ' R')} · {printable(report.expectancy_bps, ' bp')}</small></td><td>{printable(report.profit_factor)}</td><td>수수료 {report.fees}<br />슬리피지 {report.slippage}</td><td>{report.maximum_drawdown} USDT</td><td>{report.median_hold_ms === null ? '표본 없음' : `${(report.median_hold_ms / 1000).toFixed(1)}초`}<br />{report.p90_hold_ms === null ? '—' : `${(report.p90_hold_ms / 1000).toFixed(1)}초`}</td><td>{report.sample_status}<small>레짐 {report.regime_count}개 · 기간 {report.sample_span_days}일</small></td><td>{report.recommendation}<small>참고용</small></td></tr>
      }))}</tbody></table>{strategies.length === 0 ? <p className="empty-copy">전략 성과 레지스트리를 불러오는 중입니다.</p> : null}</div></details>
      <details className="panel advanced-details shadow-performance"><summary>독립 shadow 가상계좌 보기</summary><div className="shadow-grid">{shadowAccounts.map((account) => <article key={`${account.strategy_id}-${account.profile}`}><span>{account.strategy_id} · {account.profile}</span><b>{account.current_equity_usdt} USDT</b><small>순손익 {account.realized_pnl_usdt} · 수수료 {account.fees_usdt} · 슬리피지 {account.slippage_usdt} · 최대 낙폭 {account.maximum_drawdown_usdt}</small></article>)}</div></details>
      <section className="panel research-warning"><h3>통계적 주의</h3><p>{performance.win_rate}</p><p>짧은 표본을 연환산하지 않으며, 오프라인 DEMO 결과를 실제 전략 수익성의 근거로 사용하지 않습니다. 방향·종목·레짐별 차이는 표본이 쌓인 뒤에만 상대 비교합니다.</p></section>
    </section>
  )
}
