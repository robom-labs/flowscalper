// V6 전 화면에서 PAPER 안전 상태와 한 개의 진입 일시정지 동작을 유지한다.
import { formatUsdt } from '../format'
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  connected: boolean
  safetyVerified: boolean
  connectionState: 'CONNECTING' | 'CONNECTED' | 'RECONNECTING'
  onHome: () => void
  onPauseToggle: () => void
  immediateAction: 'pause' | 'resume' | null
}

export function SafetyHeader({ data, connected, safetyVerified, connectionState, onHome, onPauseToggle, immediateAction }: Props) {
  const { status } = data
  const action = data.operation_status.recommended_action
  const actionable = action === 'PAUSE' || action === 'RESUME'
  const actionLabel = immediateAction === 'pause'
    ? '멈추는 중…'
    : immediateAction === 'resume'
      ? '다시 시작하는 중…'
      : action === 'PAUSE'
        ? '새 진입 잠시 멈추기'
        : action === 'RESUME'
          ? '새 진입 다시 시작'
          : '자동 안전관리 중'
  const connectionLabel = connected
    ? '화면 연결됨'
    : connectionState === 'CONNECTING'
      ? '화면 연결 중'
      : '화면 복구 중'
  const pnl = Number(status.realized_pnl_usdt)

  return (
    <header className="topbar">
      <button
        type="button"
        className="brand-lockup brand-home"
        onClick={onHome}
        aria-label="시장 메인으로 이동하고 최신 상태 불러오기"
        title="시장 메인으로 · 최신 상태 새로고침"
      >
        <h1>ROBOM FlowScalper</h1>
      </button>
      <div className="header-status" aria-label="운영 상태">
        <span className={safetyVerified ? 'paper-lock' : 'connection-off'}>
          {safetyVerified ? 'PAPER · 실제 주문 0' : '안전 상태 미확인 · 조작 잠금'}
        </span>
        <span className={connected ? 'connection-on' : 'connection-off'}>{connectionLabel}</span>
        <span className={data.operation_status.market_observation_active ? 'connection-on' : 'connection-off'}>
          자동 관찰 · {data.operation_status.market_observation_active ? '시작됨' : '시작 전'}
        </span>
        <span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>순손익 {formatUsdt(status.realized_pnl_usdt, { signed: true })}</span>
        <span>보유 {data.focus_positions.length}건</span>
        <button type="button" className="header-action" disabled={!connected || !safetyVerified || !actionable || immediateAction !== null} onClick={onPauseToggle}>{actionLabel}</button>
      </div>
    </header>
  )
}
