// 현재 Run의 불변 위험 가정과 PAPER 제어를 명확히 표시한다.
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  onPauseToggle: () => void
  onNewRun: () => void
}

export function RiskPage({ data, onPauseToggle, onNewRun }: Props) {
  const risk = data.risk
  return (
    <section aria-labelledby="risk-heading">
      <div className="page-heading"><div><p className="section-kicker">FAIL-CLOSED LIMITS</p><h2 id="risk-heading">위험관리</h2></div><span className="page-note">현재 Run 가정은 변경 불가</span></div>
      <div className="risk-layout"><section className="panel"><h3>Run 위험 가정</h3><dl className="detail-list"><div><dt>거래당 위험</dt><dd>{risk.risk_per_trade}</dd></div><div><dt>최대 동시 포지션</dt><dd>{risk.max_positions}</dd></div><div><dt>일간 손실한도</dt><dd>{risk.daily_loss_limit}</dd></div><div><dt>주간 손실한도</dt><dd>{risk.weekly_loss_limit}</dd></div><div><dt>Drawdown lock</dt><dd>{risk.drawdown_lock}</dd></div></dl></section><section className="panel"><h3>활성 잠금</h3><div className="lock-list">{risk.active_locks.map((lock) => <span key={lock}>{lock}</span>)}</div><p>평균단가 낮추기, 마틴게일, 피라미딩, stop 확대는 구조적으로 금지됩니다.</p></section><section className="panel risk-actions"><h3>PAPER 제어</h3><button type="button" className="secondary-button" onClick={onPauseToggle}>{data.paused ? '신규 진입 재개' : '신규 진입 일시정지'}</button><button type="button" className="danger-button" onClick={onNewRun}>기존 기록 보존 후 새 Run</button><p>새 Run은 현재 기록을 삭제하지 않고 별도 실험으로 생성합니다.</p></section></div>
    </section>
  )
}

