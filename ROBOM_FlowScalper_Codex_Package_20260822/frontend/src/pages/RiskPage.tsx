// 현재 Run의 불변 위험 가정과 PAPER 제어를 명확히 표시한다.
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  onPauseToggle: () => void
  onNewRun: () => void
}

export function RiskPage({ data, onPauseToggle, onNewRun }: Props) {
  const risk = data.risk
  const lockLabels: Record<string, string> = {
    PAPER_ONLY: '실제 주문 차단',
    ENTRY_LOCK_DATA_NOT_VERIFIED: '시장데이터 검증 대기',
    CRITICAL_MARKET_LAG_ENTRY_LOCK: '데이터 지연으로 신규 진입 잠금',
  }
  return (
    <section aria-labelledby="risk-heading">
      <div className="page-heading"><div><p className="section-kicker">SAFETY</p><h2 id="risk-heading">안전 설정</h2><p className="heading-help">데이터나 조건이 불확실하면 새 모의거래를 자동으로 막습니다.</p></div><span className="page-note">현재 기록의 기준은 유지됩니다.</span></div>
      <div className="risk-layout"><section className="panel"><h3>Run 위험 가정</h3><dl className="detail-list"><div><dt>거래당 위험</dt><dd>{risk.risk_per_trade}</dd></div><div><dt>최대 동시 포지션</dt><dd>{risk.max_positions}</dd></div><div><dt>일간 손실한도</dt><dd>{risk.daily_loss_limit}</dd></div><div><dt>주간 손실한도</dt><dd>{risk.weekly_loss_limit}</dd></div><div><dt>낙폭 잠금</dt><dd>{risk.drawdown_lock}</dd></div></dl></section><section className="panel"><h3>현재 적용 중인 안전장치</h3><div className="lock-list">{risk.active_locks.map((lock) => <span key={lock}>{lockLabels[lock] ?? lock}</span>)}</div><p>손실 포지션 물타기, 배팅액을 늘리는 마틴게일, 추가 진입, 손절선 확대는 구조적으로 금지됩니다.</p></section><section className="panel risk-actions"><h3>PAPER 제어</h3><button type="button" className="secondary-button" onClick={onPauseToggle}>{data.paused ? '신규 진입 재개' : '신규 진입 일시정지'}</button><button type="button" className="danger-button" onClick={onNewRun}>기존 기록 보존 후 새 Run</button><p>새 Run은 현재 기록을 삭제하지 않고 1,000 USDT, 손익·수수료·거래 0에서 별도 실험으로 시작합니다.</p></section></div>
    </section>
  )
}
