// Strategy League 독립계좌 성과와 Shared Capital Benchmark를 분리해 비교한다.
import { PerformanceCurve } from '../components/PerformanceCurve'
import { orderedStrategies } from '../strategyPresentation'
import type { DashboardData, HistoryRow, LeagueAccount, StrategyRow } from '../types'

type Props = {
  data: DashboardData
  strategies: StrategyRow[]
  leagueAccounts: LeagueAccount[]
  history: HistoryRow[]
}

function printable(value: string | number | null, suffix = '') {
  return value === null ? '표본 없음' : `${value}${suffix}`
}

export function PerformancePage({ data, strategies, leagueAccounts, history }: Props) {
  const base = leagueAccounts.filter((account) => account.profile === 'BASE')
  const currentTotal = base.reduce((total, account) => total + Number(account.current_equity_usdt), 0)
  const startingTotal = base.reduce((total, account) => total + Number(account.starting_equity_usdt), 0)
  const highest = [...base].sort((left, right) => Number(right.current_equity_usdt) - Number(left.current_equity_usdt))[0]
  const lowest = [...base].sort((left, right) => Number(left.current_equity_usdt) - Number(right.current_equity_usdt))[0]
  const ordered = orderedStrategies(strategies)
  const strategyName = (strategyId: string) => ordered.find((strategy) => strategy.strategy_id === strategyId)?.display_name_ko ?? strategyId
  const summary = [
    ['BASE 독립계좌 합계', `${currentTotal.toFixed(2)} USDT`],
    ['BASE 누적 순손익', `${(currentTotal - startingTotal).toFixed(4)} USDT`],
    ['BASE 총 수수료', `${base.reduce((total, account) => total + Number(account.fees_usdt), 0).toFixed(4)} USDT`],
    ['BASE 총 슬리피지', `${base.reduce((total, account) => total + Number(account.slippage_usdt), 0).toFixed(4)} USDT`],
    ['BASE 완료 거래', `${base.reduce((total, account) => total + account.trade_count, 0)}건`],
    ['가장 높은 BASE', highest ? `${strategyName(highest.strategy_id)} · ${highest.current_equity_usdt}` : '표본 없음'],
    ['가장 낮은 BASE', lowest ? `${strategyName(lowest.strategy_id)} · ${lowest.current_equity_usdt}` : '표본 없음'],
  ]
  return (
    <section aria-labelledby="performance-heading">
      <div className="page-heading"><div><p className="section-kicker">STRATEGY LEAGUE RESULTS</p><h2 id="performance-heading">성과</h2><p className="heading-help">여섯 전략의 독립 가상계좌를 같은 기준으로 비교합니다.</p></div><span className="calibrating">표본이 적으면 데이터 모으는 중</span></div>
      <p className="league-warning">6개 계좌 합계는 하나의 1,000 USDT 공동계좌 수익이 아닙니다.</p>
      <section className="analytics-grid league-analytics">{summary.map(([label, value]) => <article className="panel analytics-card" key={label}><span>{label}</span><b>{value}</b></article>)}</section>
      <section className="panel strategy-performance-panel"><div className="panel-title"><div><p className="section-kicker">BASE · STRESS</p><h3>전략별 독립계좌</h3></div><span>총 12계좌</span></div><div className="table-scroll"><table className="performance-table"><thead><tr><th>전략</th><th>계좌</th><th>현재자산</th><th>거래·승률</th><th>기대값</th><th>Profit Factor</th><th>비용</th><th>낙폭</th><th>표본</th></tr></thead><tbody>{ordered.flatMap((strategy) => (['BASE', 'STRESS'] as const).map((profile) => {
        const report = strategy.performance[profile]
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === profile)
        return <tr key={`${strategy.strategy_id}-${profile}`}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko}</small></td><td>{profile}</td><td>{account?.current_equity_usdt ?? '1000'} USDT</td><td>{report.sample_size}건<small>{report.win_rate === null ? '승률 표본 없음' : `승률 ${(Number(report.win_rate) * 100).toFixed(1)}%`}</small></td><td>{printable(report.expectancy_usdt, ' USDT')}<small>{printable(report.expectancy_r, ' R')} · {printable(report.expectancy_bps, ' bp')}</small></td><td>{printable(report.profit_factor)}</td><td>수수료 {account?.fees_usdt ?? report.fees}<small>슬리피지 {account?.slippage_usdt ?? report.slippage}</small></td><td>{account?.maximum_drawdown_usdt ?? report.maximum_drawdown} USDT</td><td>{report.sample_status}<small>{report.recommendation} · 참고용</small></td></tr>
      }))}</tbody></table></div></section>
      <section className="panel performance-curve-panel"><div className="panel-title"><div><p className="section-kicker">SHARED CAPITAL BENCHMARK</p><h3>공동계좌 자산곡선</h3></div><span>시작자산 1,000 USDT · 별도 기준</span></div><PerformanceCurve history={history.filter((row) => row.profile === 'BASE')} /><dl className="benchmark-summary"><div><dt>현재자산</dt><dd>{data.status.current_equity_usdt.toFixed(2)} USDT</dd></div><div><dt>순손익</dt><dd>{(data.status.current_equity_usdt - data.status.starting_equity_usdt).toFixed(4)} USDT</dd></div><div><dt>완료 거래</dt><dd>{data.status.trade_count}건</dd></div></dl></section>
      <section className="panel research-warning"><h3>통계적 주의</h3><p>표본이 없는 승률·기대값·Profit Factor는 계산하지 않습니다. 짧은 표본을 연환산하거나 수익 보장처럼 해석하지 않습니다.</p></section>
    </section>
  )
}
