// 전략별 모의계좌 성과는 쉬운 핵심 결과를 먼저 보여주고 연구 지표는 접어서 분리한다.
import { PerformanceCurve } from '../components/PerformanceCurve'
import { costProfileLabel, formatDurationMs, formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { orderedStrategies } from '../strategyPresentation'
import type { DashboardData, HistoryRow, LeagueAccount, StrategySummaryRow } from '../types'

type Props = {
  data: DashboardData
  strategies: StrategySummaryRow[]
  leagueAccounts: LeagueAccount[]
  history: HistoryRow[]
}

function evidenceLabel(sampleSize: number, status: string) {
  if (sampleSize < 30) return `자료 모으는 중 · ${sampleSize}/30건`
  if (status.includes('PROVEN') && !status.includes('NOT')) return '검증 기준 통과'
  if (status.includes('FAIL') || status.includes('REJECT')) return '검증 기준 미달'
  return '추가 검증 필요'
}

export function StrategyPerformancePanel({ data, strategies, leagueAccounts, history }: Props) {
  const ordered = orderedStrategies(strategies)
  const analyticsReady = data.system.dashboard_trade_cache_ready !== false
  return (
    <section aria-label="전략 성과">
      <p className="profile-scope-note">자산은 이번 실행, 승률과 통계는 현재 전략 버전의 공개시장 모의거래만 사용합니다.</p>
      {!analyticsReady ? <p className="profile-scope-note" role="status">과거 거래통계를 현재 전략 버전과 나누고 있습니다. 준비가 끝나기 전에는 승률·기대값·순위를 표시하지 않습니다.</p> : null}
      <section className="panel strategy-performance-panel">
        <div className="panel-title"><div><p className="section-kicker">기본 비용 · 보수 비용</p><h3>전략별 가상계좌</h3></div><span>총 {leagueAccounts.length}계좌</span></div>
        <div className="table-scroll"><table className="performance-table"><thead><tr><th>전략·비용</th><th>이번 실행</th><th>완료·승률</th><th>거래당 기대값</th><th>비용·낙폭</th><th>검증 상태</th></tr></thead><tbody>{ordered.flatMap((strategy) => (['BASE', 'STRESS'] as const).map((profile) => {
          const report = strategy.performance[profile]
          const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === profile)
          const runPnl = account ? Number(account.current_equity_usdt) - Number(account.starting_equity_usdt) : 0
          return <tr key={`${strategy.strategy_id}-${profile}`}>
            <td data-label="전략·비용"><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko} · {costProfileLabel(profile)}</small></td>
            <td data-label="이번 실행"><strong className={runPnl > 0 ? 'positive' : runPnl < 0 ? 'negative' : ''}>{formatUsdt(runPnl, { signed: true })}</strong><small>현재 자산 {formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</small></td>
            <td data-label="완료·승률">{analyticsReady ? <><strong>{report.sample_size}건</strong><small>{report.win_rate === null ? '아직 승률 표본 없음' : `승률 ${formatPercentFraction(report.win_rate)} · 승 ${report.wins} · 패 ${report.losses}`}</small></> : '불러오는 중'}</td>
            <td data-label="거래당 기대값">{analyticsReady ? <><strong>{formatUsdt(report.expectancy_usdt)}</strong><small>비용을 뺀 거래당 평균</small></> : '—'}</td>
            <td data-label="비용·낙폭">{analyticsReady ? <><strong>{formatUsdt(Number(report.fees) + Number(report.slippage))}</strong><small>최대 낙폭 {formatUsdt(report.maximum_drawdown)}</small></> : '—'}</td>
            <td data-label="검증 상태">{analyticsReady ? <><strong>{evidenceLabel(report.sample_size, report.sample_status)}</strong><details className="row-advanced-details"><summary>고급 통계</summary><span>이익합계/손실합계 {formatRatio(report.profit_factor)} · 기대값 {formatRatio(report.expectancy_r, ' R')} / {formatRatio(report.expectancy_bps, ' bp')}</span><span>{report.median_hold_ms === null ? '보유시간 표본 없음' : `보유 중앙 ${formatDurationMs(report.median_hold_ms)}`}</span><span>{report.median_time_to_tp1_ms === null ? '1차 목표 표본 없음' : `1차 목표 중앙 ${formatDurationMs(report.median_time_to_tp1_ms)} · ${report.tp1_sample_size}건`}</span><span>{report.trail_activation_count > 0 ? `추적 활성 ${report.trail_activation_count}건 · 남은 수량 관리 ${report.runner_count}건 · 1차 목표 체결 ${formatPercentFraction(report.tp1_fill_rate)}` : '추적 익절 표본 없음'}</span><span>{report.trail_activation_count > 0 ? `남은 수량 순기여 ${formatUsdt(report.runner_net_contribution_usdt, { signed: true })} · 되돌림 중앙 ${formatUsdt(report.median_peak_giveback_usdt)} / 상위 10% ${formatUsdt(report.p90_peak_giveback_usdt)}` : '추적 후 수익·되돌림 표본 없음'}</span><span>수수료 {formatUsdt(report.fees)} · 가격차이 {formatUsdt(report.slippage)} · 추적 종료 비용 {formatUsdt(report.trail_trigger_slippage_usdt)}</span><span>과거 버전 {report.excluded_prior_version_samples}건 제외</span><span>기술 판단 코드 {report.sample_status} · {report.recommendation}</span></details></> : '확인 중'}</td>
          </tr>
        }))}</tbody></table></div>
      </section>
      <section className="panel performance-curve-panel"><div className="panel-title"><div><p className="section-kicker">공동 가상계좌</p><h3>공동계좌 자산 흐름</h3></div><span>시작자산 1,000 USDT · 별도 기준</span></div><PerformanceCurve history={history.filter((row) => row.profile === 'BASE')} /><dl className="benchmark-summary"><div><dt>현재자산</dt><dd>{formatUsdt(data.status.current_equity_usdt, { equity: true })}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(data.status.current_equity_usdt - data.status.starting_equity_usdt, { signed: true })}</dd></div><div><dt>완료 거래</dt><dd>{data.status.trade_count}건</dd></div></dl></section>
      <section className="panel research-warning"><h3>꼭 알아두세요</h3><p>거래가 30건보다 적으면 승률과 기대값으로 전략 우열을 정하지 않습니다. 테스트 통과도 수익 보장을 뜻하지 않습니다.</p></section>
    </section>
  )
}
