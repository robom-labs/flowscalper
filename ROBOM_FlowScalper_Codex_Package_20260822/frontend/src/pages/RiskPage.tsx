// 공동계좌와 전략별 가상계좌의 서로 다른 PAPER 위험계약을 분리해 표시한다.
import type { DashboardData } from '../types'

type Props = { data: DashboardData; onPauseToggle: () => void; onNewRun: () => void; immediateAction: 'pause' | 'resume' | null }

export function RiskPage({ data, onPauseToggle, onNewRun, immediateAction }: Props) {
  const shared = data.risk.shared_capital
  const league = data.risk.strategy_league
  const entryAction = data.operation_status.recommended_action
  const entryButtonLabel = immediateAction === 'pause'
    ? '잠시 멈추는 중…'
    : immediateAction === 'resume'
      ? '다시 시작하는 중…'
      : entryAction === 'PAUSE'
    ? '새 진입 잠시 멈추기'
    : entryAction === 'RESUME'
      ? '새 진입 다시 시작하기'
      : data.operation_status.state === 'READY'
        ? '자동 관찰 시작 전'
        : data.operation_status.state === 'SAFETY_BLOCKED'
          ? '안전 확인 필요'
          : data.operation_status.state === 'RECONNECTING'
            ? '시장 다시 연결 중'
            : '자동 안전대기 중'
  const lockLabels: Record<string, string> = {
    PAPER_ONLY: '실제 주문 차단',
    ENTRY_LOCK_DATA_NOT_VERIFIED: '시장데이터 검증 대기',
    CRITICAL_MARKET_LAG_ENTRY_LOCK: '데이터 지연으로 신규 진입 잠금',
    STORAGE_PRESSURE_ENTRY_LOCK: '저장공간 안전잠금',
    RECOVERY_FAIL_CLOSED: '복구 안전잠금',
  }
  return (
    <section aria-labelledby="risk-heading">
      <div className="page-heading"><div><p className="section-kicker">SAFETY</p><h2 id="risk-heading">안전 설정</h2><p className="heading-help">데이터나 조건이 불확실하면 새 모의거래를 자동으로 막습니다.</p></div><span className="page-note">이번 화면에서는 위험값을 바꾸지 않습니다.</span></div>
      <div className="risk-contract-grid">
        <section className="panel"><p className="section-kicker">공동계좌 기준</p><h3>공동계좌</h3><dl className="detail-list"><div><dt>시작자산</dt><dd>{shared.starting_equity_usdt} USDT</dd></div><div><dt>포지션당 위험</dt><dd>{shared.risk_per_position}</dd></div><div><dt>최대 동시 포지션</dt><dd>{shared.max_positions}개</dd></div><div><dt>일간 손실한도</dt><dd>{shared.daily_loss_limit}</dd></div><div><dt>주간 손실한도</dt><dd>{shared.weekly_loss_limit}</dd></div><div><dt>낙폭 잠금</dt><dd>{shared.drawdown_lock}</dd></div></dl></section>
        <section className="panel"><p className="section-kicker">전략별 가상계좌</p><h3>각 독립 전략계좌</h3><dl className="detail-list"><div><dt>계좌 수 · 시작자산</dt><dd>{league.account_count}개 · 각 {league.starting_equity_per_account_usdt} USDT</dd></div><div><dt>포지션당 위험</dt><dd>{league.risk_per_position}</dd></div><div><dt>최대 종목</dt><dd>{league.max_positions_per_account}개</dd></div><div><dt>총 계획위험</dt><dd>{league.maximum_total_open_risk}</dd></div><div><dt>최대 유효 레버리지</dt><dd>{league.maximum_effective_leverage}</dd></div><div><dt>실행가능 깊이 사용</dt><dd>{league.maximum_depth_fraction}</dd></div><div><dt>일 · 주 손실한도</dt><dd>{league.daily_loss_limit} · {league.weekly_loss_limit}</dd></div><div><dt>낙폭 잠금</dt><dd>{league.drawdown_lock}</dd></div><div><dt>BASE 진입 · 종료</dt><dd>{league.base_entry_fee} · {league.base_exit_fee}</dd></div><div><dt>STRESS 진입 · 종료</dt><dd>{league.stress_entry_fee} · {league.stress_exit_fee}</dd></div></dl><p className="risk-explainer">5배는 항상 사용하는 값이 아니라 위험계산 뒤의 최대 상한입니다.</p></section>
      </div>
      <div className="risk-layout lower-risk-layout"><section className="panel"><h3>현재 적용 중인 안전장치</h3><div className="lock-list">{[...new Set(data.risk.active_locks)].map((lock) => <span key={lock}>{lockLabels[lock] ?? lock}</span>)}</div><p>손실 포지션 물타기, 마틴게일, 피라미딩, 손절선 확대는 구조적으로 금지됩니다.</p></section><section className="panel risk-actions"><h3>PAPER 제어</h3><button type="button" className="secondary-button" onClick={onPauseToggle} disabled={immediateAction !== null || (entryAction !== 'PAUSE' && entryAction !== 'RESUME')}>{entryButtonLabel}</button><p>{data.operation_status.detail_ko}</p><button type="button" className="danger-button" onClick={onNewRun}>기존 기록 보존 후 새 PAPER Run</button><p>새 Run은 과거 기록을 삭제하지 않고 별도 실험으로 시작합니다.</p></section></div>
    </section>
  )
}
