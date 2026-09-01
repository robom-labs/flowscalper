// V6 전 화면에서 자동 PAPER 진입과 안전 잠금을 별도 상태로 보여준다.
import { formatUsdt } from '../format'
import type { DashboardData } from '../types'

type Props = {
  data: DashboardData
  connected: boolean
  safetyVerified: boolean
  connectionState: 'CONNECTING' | 'CONNECTED' | 'RECONNECTING'
  onHome: () => void
}

export function SafetyHeader({ data, connected, safetyVerified, connectionState, onHome }: Props) {
  const { status } = data
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
        <span className={data.operation_status.paper_entry_active ? 'connection-on' : 'connection-off'}>
          {data.operation_status.paper_entry_active ? '자동 진입 · 항상 허용' : '자동 진입 · 안전 대기'}
        </span>
        <span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>순손익 {formatUsdt(status.realized_pnl_usdt, { signed: true })}</span>
        <span>보유 {data.focus_positions.length}건</span>
      </div>
    </header>
  )
}
