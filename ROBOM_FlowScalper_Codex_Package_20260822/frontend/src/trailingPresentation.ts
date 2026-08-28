// PAPER 트레일링의 내부 상태를 비전문가용 한국어로 바꾼다.
import type { TrailingPositionState } from './types'

const STATE_LABELS: Record<string, string> = {
  ENTRY_PENDING: '진입 체결 대기',
  INITIAL_PROTECTION: '초기 손절 보호 중',
  PROFIT_ACTIVATION_PENDING: '추적 익절 활성화 대기',
  TRAIL_ARMED: '추적 익절 준비 완료',
  PARTIAL_TP_PENDING: '1차 익절 체결 대기',
  RUNNER_ACTIVE: '남은 수량 추적 중',
  TRAIL_EXIT_PENDING: '추적 종료 체결 대기',
  CLOSED: '거래 종료',
}

export function trailingStateLabel(trailing: TrailingPositionState | undefined): string {
  if (!trailing?.enabled) return '고정 익절·손절 관리'
  return STATE_LABELS[trailing.state] ?? '추적 익절 상태 확인 중'
}

export function trailingSummary(trailing: TrailingPositionState | undefined): string {
  const label = trailingStateLabel(trailing)
  if (!trailing?.enabled) return label
  const trail = trailing.current_trail ? ` · 보호선 ${trailing.current_trail}` : ''
  const adverse = trailing.adverse_active ? ' · 추세 약화 지속 확인' : ''
  return `${label}${trail}${adverse}`
}
