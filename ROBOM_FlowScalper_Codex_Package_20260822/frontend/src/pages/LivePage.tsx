// 비전문가가 현재 PAPER 관찰과 거래 수를 한눈에 확인하는 짧은 요약 화면이다.
import type { DashboardData, PageId } from '../types'
import { formatUsdt } from '../format'
import { strategyWaitReasonLabel } from '../strategyPresentation'

type Props = { data: DashboardData; onNavigate: (page: PageId) => void }

export function LivePage({ data, onNavigate }: Props) {
  const open = data.league_accounts.filter((row) => row.profile === 'BASE').reduce((sum, row) => sum + row.open_positions, 0)
  const researchTrades = data.strategies.reduce((sum, strategy) => (
    sum + strategy.performance.BASE.sample_size + strategy.performance.STRESS.sample_size
  ), 0)
  const evaluationCount = Number(data.system.strategy_evaluation_count ?? 0)
  const qualifiedSignalCount = Number(data.system.qualified_signal_count ?? 0)
  const enabledStrategies = data.strategies.filter((strategy) => (
    strategy.mode !== 'OFF' && (strategy.long_enabled || strategy.short_enabled)
  ))
  const currentWaitReasons = [...new Set(
    enabledStrategies.flatMap((strategy) => strategy.latest_reasons.map(strategyWaitReasonLabel)),
  )].slice(0, 3)
  const entryReviewNeeded = data.operation_status.paper_entry_active
    && open === 0
    && evaluationCount > 0
    && qualifiedSignalCount === 0
  const activityMessage = data.operation_status.paper_entry_active
    ? open > 0
      ? `현재 ${open}건을 관리하고 있습니다.`
      : entryReviewNeeded
        ? '시장 처리는 정상이나 이번 작동 시작 후 진입 조건을 통과한 신호가 없습니다. 전략별 탈락 이유를 확인해야 합니다.'
        : '현재 보유는 없으며 통과한 신호와 결과는 거래 기록에 남아 있습니다.'
    : data.operation_status.detail_ko
  const activityTitle = entryReviewNeeded ? '진입 조건 검토 필요' : data.operation_status.title_ko

  return (
    <section aria-labelledby="summary-heading">
      <div className="page-heading">
        <div><p className="section-kicker">한눈에 보기</p><h2 id="summary-heading">프로그램 요약</h2><p className="heading-help">실제 주문 없이 공개시장 데이터를 보며 PAPER 결과만 기록합니다.</p></div>
        <button type="button" className="primary-button" onClick={() => onNavigate('terminal')}>시장 화면 열기</button>
      </div>
      <section className="metric-strip">
        <article><span>현재 자산</span><b>{formatUsdt(data.status.current_equity_usdt, { equity: true })}</b></article>
        <article><span>보유 중</span><b>{open}건</b></article>
        <article><span>통합계좌 완료</span><b>{data.status.trade_count}건</b></article>
        <article><span>정밀 분석</span><b>{data.status.deep_symbols || data.scanner.length}종목</b></article>
        <article><span>실제 주문</span><b>0건</b></article>
      </section>
      <section className={`panel live-activity-card${entryReviewNeeded ? ' entry-review-needed' : ''}`} aria-labelledby="live-activity-title" role="status">
        <div>
          <p className="section-kicker">지금 무엇을 하나요?</p>
          <h3 id="live-activity-title">{activityTitle}</h3>
          <p>{activityMessage}</p>
          {entryReviewNeeded ? <p className="entry-wait-summary">현재 주요 대기 이유 · {currentWaitReasons.join(' · ') || '전략 세부 조건 미충족'}</p> : null}
        </div>
        <dl>
          <div><dt>시장 판정</dt><dd>{evaluationCount.toLocaleString('ko-KR')}회</dd></div>
          <div><dt>이번 작동 후 진입 통과</dt><dd>{qualifiedSignalCount.toLocaleString('ko-KR')}건</dd></div>
          <div><dt>현재 전략 연구거래</dt><dd>{researchTrades.toLocaleString('ko-KR')}건</dd></div>
        </dl>
        <div className="live-activity-actions">
          {entryReviewNeeded ? <button type="button" className="table-button" onClick={() => onNavigate('strategies')}>0건 원인 보기</button> : null}
          <button type="button" className="secondary-button" onClick={() => onNavigate('history')}>거래 기록 보기</button>
        </div>
      </section>
      <section className="panel shared-benchmark-card">
        <div><p className="section-kicker">통합 가상계좌</p><h3>1,000 USDT PAPER 기준</h3><p>전략별 가상계좌와 분리해 보는 통합 기준입니다.</p></div>
        <dl><div><dt>시작자산</dt><dd>{formatUsdt(data.status.starting_equity_usdt, { equity: true })}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(data.status.realized_pnl_usdt, { signed: true })}</dd></div><div><dt>수수료</dt><dd>{formatUsdt(data.status.cumulative_fees_usdt)}</dd></div><div><dt>슬리피지</dt><dd>{formatUsdt(data.status.cumulative_slippage_usdt)}</dd></div></dl>
      </section>
    </section>
  )
}
