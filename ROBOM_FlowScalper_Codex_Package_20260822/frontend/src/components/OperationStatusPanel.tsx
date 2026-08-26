// 초보자가 시작 결과와 자동 안전복구 상태를 한눈에 확인하는 운영 상태 패널이다.
import type { ControlOperation, DashboardData } from '../types'

type Props = {
  data: DashboardData
  busy: boolean
  immediateAction: 'pause' | 'resume' | null
  operation: ControlOperation | null
  onStartLive: () => void
  onStartDemo: () => void
  onPauseToggle: () => void
  onCancel: () => void
  onRetry: () => void
}

const activeOperationStates = new Set(['REQUESTED', 'PREPARING', 'CONNECTING_PRIMARY', 'CONNECTING_FALLBACK', 'CANCELLING'])

export function OperationStatusPanel({ data, busy, immediateAction, operation, onStartLive, onStartDemo, onPauseToggle, onCancel, onRetry }: Props) {
  const status = data.operation_status
  const connecting = Boolean(operation && activeOperationStates.has(operation.state))
  const failed = operation?.state === 'FAILED_RETRYABLE' || operation?.state === 'FAILED_BLOCKED'
  const stateClass = connecting ? 'connecting' : failed ? 'blocked' : status.state.toLowerCase().replaceAll('_', '-')
  const title = connecting ? '연결 중' : failed ? '시작하지 못했습니다' : status.title_ko
  const detail = connecting || failed ? operation?.stage_ko ?? status.detail_ko : status.detail_ko
  const lag = status.lag_p95_ms === null ? '측정 대기' : `${Math.round(status.lag_p95_ms).toLocaleString('ko-KR')} ms`

  return <section className={`operation-status-card ${stateClass}`} aria-live="polite" aria-label="프로그램 작동 상태">
    <div className="operation-state-main">
      <span className="operation-pulse" aria-hidden="true" />
      <div><strong>{title}</strong><p>{detail}</p></div>
    </div>
    <div className="operation-facts" aria-label="세부 작동 상태">
      <span><small>시장 관찰</small><b>{connecting ? '연결 중' : status.market_observation_active ? '계속 작동' : '대기'}</b></span>
      <span><small>새 PAPER 진입</small><b>{status.paper_entry_active ? '작동' : status.state === 'MANUALLY_PAUSED' ? '내가 멈춤' : status.state === 'READY' ? '시작 전' : '안전 대기'}</b></span>
      <span><small>자동 복구</small><b>{status.automatic_recovery ? '켜짐' : '해당 없음'}</b></span>
      <span><small>데이터 지연 P95</small><b>{lag}</b></span>
    </div>
    <div className="operation-actions">
      {status.state === 'READY' && !connecting && !failed ? <><button type="button" className="operation-primary" disabled={busy} onClick={onStartLive}>자동 관찰 시작</button><button type="button" className="operation-secondary" disabled={busy} onClick={onStartDemo}>샘플로 보기</button></> : null}
      {connecting ? <button type="button" className="operation-secondary" onClick={onCancel}>연결 취소</button> : null}
      {operation?.state === 'FAILED_RETRYABLE' ? <button type="button" className="operation-primary" onClick={onRetry}>다시 연결</button> : null}
      {status.recommended_action === 'PAUSE' && !connecting ? <button type="button" className="operation-secondary" disabled={immediateAction !== null} onClick={onPauseToggle}>{immediateAction === 'pause' ? '잠시 멈추는 중…' : status.state === 'DEMO_RUNNING' ? '샘플 멈춤' : '새 진입 잠시 멈춤'}</button> : null}
      {status.recommended_action === 'RESUME' && !connecting ? <button type="button" className="operation-primary" disabled={immediateAction !== null} onClick={onPauseToggle}>{immediateAction === 'resume' ? '다시 시작하는 중…' : status.state === 'DEMO_PAUSED' ? '샘플 다시 재생' : '새 진입 다시 시작'}</button> : null}
      {status.state === 'SAFETY_WAITING' ? <span className="operation-auto-note">정상화되면 자동으로 다시 시작합니다.</span> : null}
      {status.state === 'SAFETY_BLOCKED' ? <span className="operation-auto-note blocked">고급진단에서 원인을 확인하세요.</span> : null}
    </div>
  </section>
}
