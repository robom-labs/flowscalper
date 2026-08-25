// 전략별 독립계좌 성과와 공동계좌 기준을 분리해 비교한다.
import { PerformanceCurve } from '../components/PerformanceCurve'
import { formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { orderedStrategies } from '../strategyPresentation'
import type { DashboardData, HistoryRow, LeagueAccount, StrategyRow } from '../types'

type Props = {
  data: DashboardData
  strategies: StrategyRow[]
  leagueAccounts: LeagueAccount[]
  history: HistoryRow[]
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
    ['이번 Run BASE 계좌 합계', formatUsdt(currentTotal, { equity: true })],
    ['이번 Run BASE 순손익', formatUsdt(currentTotal - startingTotal, { signed: true })],
    ['이번 Run BASE 수수료', formatUsdt(base.reduce((total, account) => total + Number(account.fees_usdt), 0))],
    ['이번 Run BASE 슬리피지', formatUsdt(base.reduce((total, account) => total + Number(account.slippage_usdt), 0))],
    ['이번 Run BASE 완료 거래', `${base.reduce((total, account) => total + account.trade_count, 0)}건`],
    ['이번 Run 가장 높은 BASE', highest ? `${strategyName(highest.strategy_id)} · ${formatUsdt(highest.current_equity_usdt, { equity: true })}` : '표본 없음'],
    ['이번 Run 가장 낮은 BASE', lowest ? `${strategyName(lowest.strategy_id)} · ${formatUsdt(lowest.current_equity_usdt, { equity: true })}` : '표본 없음'],
  ]
  return (
    <section aria-labelledby="performance-heading">
      <div className="page-heading"><div><p className="section-kicker">전략별 결과</p><h2 id="performance-heading">성과</h2><p className="heading-help">{ordered.length}개 전략의 독립 가상계좌를 같은 기준으로 비교합니다.</p></div><span className="calibrating">표본이 적으면 데이터 모으는 중</span></div>
      <p className="league-warning">BASE {base.length}개 계좌 합계는 하나의 1,000 USDT 공동계좌 수익이 아닙니다.</p>
      <p className="profile-scope-note">요약·현재자산은 이번 Run, 거래·승률·통계는 현재 전략 버전의 공개시장 PAPER 기준입니다. 교체 전 표본은 전략 상세의 제외 건수로 표시합니다.</p>
      <section className="analytics-grid league-analytics">{summary.map(([label, value]) => <article className="panel analytics-card" key={label}><span>{label}</span><b>{value}</b></article>)}</section>
      <section className="panel strategy-performance-panel"><div className="panel-title"><div><p className="section-kicker">BASE · STRESS</p><h3>전략별 독립계좌</h3></div><span>총 {leagueAccounts.length}계좌</span></div><div className="table-scroll"><table className="performance-table"><thead><tr><th>전략</th><th>계좌</th><th>이번 Run 현재자산</th><th>현재버전 거래·승률</th><th>기대값</th><th>Profit Factor</th><th>비용</th><th>낙폭</th><th>표본</th></tr></thead><tbody>{ordered.flatMap((strategy) => (['BASE', 'STRESS'] as const).map((profile) => {
        const report = strategy.performance[profile]
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === profile)
        return <tr key={`${strategy.strategy_id}-${profile}`}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko}</small></td><td>{profile}</td><td>{formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</td><td>{report.sample_size}건<small>{report.win_rate === null ? '승률 표본 없음' : `승률 ${formatPercentFraction(report.win_rate)} · ${report.wins}승 ${report.losses}패 ${report.breakevens}보합`}</small></td><td>{formatUsdt(report.expectancy_usdt)}<small>{formatRatio(report.expectancy_r, ' R')} · {formatRatio(report.expectancy_bps, ' bp')}</small></td><td>{formatRatio(report.profit_factor)}</td><td>수수료 {formatUsdt(report.fees)}<small>슬리피지 {formatUsdt(report.slippage)}</small></td><td>{formatUsdt(report.maximum_drawdown)}</td><td>{report.sample_status}<small>{report.recommendation} · 과거 버전 {report.excluded_prior_version_samples}건 제외</small></td></tr>
      }))}</tbody></table></div></section>
      <section className="panel performance-curve-panel"><div className="panel-title"><div><p className="section-kicker">공동계좌 기준</p><h3>공동계좌 자산곡선</h3></div><span>시작자산 1,000 USDT · 별도 기준</span></div><PerformanceCurve history={history.filter((row) => row.profile === 'BASE')} /><dl className="benchmark-summary"><div><dt>현재자산</dt><dd>{formatUsdt(data.status.current_equity_usdt, { equity: true })}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(data.status.current_equity_usdt - data.status.starting_equity_usdt, { signed: true })}</dd></div><div><dt>완료 거래</dt><dd>{data.status.trade_count}건</dd></div></dl></section>
      <section className="panel research-warning"><h3>통계적 주의</h3><p>표본이 없는 승률·기대값·Profit Factor는 계산하지 않습니다. 짧은 표본을 연환산하거나 수익 보장처럼 해석하지 않습니다.</p></section>
    </section>
  )
}
