// 데이터 진실성과 PAPER 전용 상태를 모든 화면 상단에 영구 표시한다.
import type { DashboardData } from '../types'
import { formatKstTime } from '../time'

type Props = {
  data: DashboardData
  connected: boolean
  connectionState: 'CONNECTING' | 'CONNECTED' | 'RECONNECTING'
  lastUpdateMs: number | null
  onSummary: () => void
}

export function SafetyHeader({ data, connected, connectionState, lastUpdateMs, onSummary }: Props) {
  const { status } = data
  const live = status.market_data_state === 'LIVE'
  const fixture = status.mode === 'DEMO_FIXTURE'
  const publicMode = status.mode === 'LIVE_SHADOW_PAPER'
  const ready = status.mode === 'READY'
  const operationState = data.operation_status.state
  const sourceLabel = live
    ? operationState === 'RUNNING' ? '작동 중'
      : operationState === 'MANUALLY_PAUSED' ? '관찰 중 · 내가 일시정지'
        : operationState === 'SAFETY_BLOCKED' ? '관찰 중 · 안전 확인 필요'
          : '관찰 중 · 자동 안전 대기'
    : publicMode
      ? '시장 연결 복구 중'
      : ready
        ? '시작 준비 완료'
        : status.mode === 'DEMO_FIXTURE'
          ? '샘플 화면'
          : '과거 데이터 재생'
  const connectionLabel = connected
    ? '화면 연결됨'
    : connectionState === 'CONNECTING'
      ? '화면 연결 중'
      : '화면 다시 연결 중'
  const openPositions = data.league_accounts
    .filter((account) => account.profile === 'BASE')
    .reduce((total, account) => total + account.open_positions, 0)
  return (
    <header className="topbar">
      <div className="brand-lockup"><h1>FlowScalper</h1><button type="button" className="summary-link" onClick={onSummary}>요약</button></div>
      <div className="header-status" aria-label="운영 상태">
        <span className={live ? 'status-dot live' : fixture ? 'status-dot fixture' : 'status-dot'}>{sourceLabel}</span>
        <span>진행 {openPositions}건</span>
        <span>정밀 {status.deep_symbols || data.scanner.length}개</span>
        <span>{lastUpdateMs ? formatKstTime(lastUpdateMs) : '시간 연결 대기'}</span>
        <span className={fixture ? 'paper-lock fixture-truth' : 'paper-lock'}>{fixture ? '샘플 PAPER · LIVE 아님 · 실제 주문 0' : 'PAPER · 실제 주문 0'}</span>
        <span className={connected ? 'connection-on' : 'connection-off'}>{connectionLabel}</span>
      </div>
    </header>
  )
}
