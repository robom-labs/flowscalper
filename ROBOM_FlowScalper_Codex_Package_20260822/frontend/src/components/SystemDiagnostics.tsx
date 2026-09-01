// 초보자 상태와 backend가 명명한 전문가 진단 행을 분리해 시스템 진실성을 표시한다.
import type { DashboardData, DiagnosticsPayload } from '../types'
import { formatKstDateTime, formatKstTime } from '../time'

type Props = { data: DashboardData; connected: boolean; lastUpdateMs: number | null }
type DiagnosticRow = DiagnosticsPayload['rows'][number]

function readable(value: unknown) {
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (value === null || value === '') return '없음'
  return String(value)
}

function readableDiagnostic(row: DiagnosticRow) {
  if (row.key === 'release_commit' && typeof row.value === 'string' && row.value.length === 40) {
    return row.value.slice(0, 12)
  }
  return readable(row.value)
}

function severityLabel(severity: DiagnosticRow['severity']) {
  if (severity === 'OK') return '정상'
  if (severity === 'WARNING') return '주의'
  if (severity === 'CRITICAL') return '오류'
  return '정보'
}

export function SystemDiagnostics({ data, connected, lastUpdateMs }: Props) {
  const marketConnected = data.status.market_data_state === 'LIVE' || data.status.market_data_state === 'FIXTURE'
  const supervisorStopped = data.system.supervisor_running === false
  const consumerStopped = data.system.consumer_running === false
  const consumerFault = data.system.consumer_fault_active === true
  const queueOverload = data.system.queue_overload_active === true
  const consumerHealthy = !supervisorStopped && !consumerStopped && !consumerFault && !queueOverload
  const healthy = marketConnected && consumerHealthy
  const marketStatusTitle = consumerStopped
    ? '시장 처리 멈춤'
    : supervisorStopped
      ? '시장 관찰 멈춤'
      : consumerFault || queueOverload
        ? '처리 안전 대기'
        : healthy
          ? '정상'
          : data.status.mode === 'READY'
            ? '시작 대기'
            : '재연결 확인 필요'
  const reconnects = Number(data.system.unplanned_reconnects ?? data.system.reconnects ?? 0)
  const plannedRotations = Number(data.system.planned_rotations ?? 0)
  const gaps = Number(data.system.sequence_gaps ?? 0)
  const storage = String(data.system.storage ?? '확인 중')
  const storageAllowed = data.system.storage_entry_allowed !== false
  const serverTimeMs = Number(data.system.server_time_ms ?? 0)
  const clockDeltaMs = serverTimeMs > 0 && lastUpdateMs
    ? Math.abs(lastUpdateMs - serverTimeMs)
    : null
  const venueClockOffsetMs = Number(data.system.venue_clock_offset_ms ?? 0)
  const clockSyncStatus = String(data.system.clock_sync_status ?? 'UNVERIFIED')
  const venueClockText = clockSyncStatus === 'SYNCED'
    ? `거래소 시각 ${venueClockOffsetMs >= 0 ? '+' : ''}${venueClockOffsetMs.toFixed(0)}ms 보정`
    : '거래소 시각 확인 중'
  const bookLag = Number(data.system.lag_p95_ms ?? data.status.processing_lag_p95_ms ?? 0)
  const tradeLag = Number(data.system.trade_lag_p95_ms ?? 0)
  const startupRecoveryState = String(data.system.startup_recovery_state ?? 'NO_RECOVERY_NEEDED')
  let startupRecovery = { title: '신규 시작', detail: '복구할 이전 Run 없음', healthy: true }
  if (startupRecoveryState === 'RECOVERY_FAIL_CLOSED') {
    startupRecovery = { title: '안전 잠금', detail: '원장 복구 검증 실패 · 신규 PAPER 진입 차단', healthy: false }
  } else if (startupRecoveryState === 'FIXTURE_STATE_RECOVERED') {
    startupRecovery = { title: '샘플 상태 복구됨', detail: '오프라인 DEMO 상태 복구 · LIVE 아님', healthy: true }
  } else if (startupRecoveryState === 'RECOVERY_REVALIDATION_LOCKED') {
    startupRecovery = { title: '상태 복구됨', detail: '새 공개호가 확인 전까지 자동 잠금', healthy: true }
  } else if (startupRecoveryState === 'RECOVERY_DEFERRED') {
    startupRecovery = { title: '복구 대기', detail: '기존 Run 상태는 변경하지 않음', healthy: true }
  }
  const paperTransitionState = String(data.system.last_paper_transition_state ?? 'NO_PAPER_TRANSITION')
  const paperTransitionSymbol = String(data.system.last_paper_transition_symbol ?? 'NONE')
  const paperTransitionAccount = String(data.system.last_paper_transition_account_id ?? 'NONE')
  const paperTransitionTitles: Record<string, string> = {
    OBSERVING: '대기 중',
    SCANNING: '대기 중',
    ARMED: '진입 준비',
    ENTRY_PENDING: '진입 대기',
    PROTECTED: '포지션 보호 중',
    EXIT_PENDING: '청산 대기',
    CLOSED: '거래 종료',
  }
  const paperTransitionTitle = paperTransitionTitles[paperTransitionState] ?? '아직 전환 없음'
  const paperTransitionDetail = paperTransitionState === 'NO_PAPER_TRANSITION'
    ? '자연 PAPER 진입·청산 전 상태'
    : `${paperTransitionSymbol} · ${paperTransitionAccount === 'MAIN:BASE' ? '공동 PAPER 계좌' : paperTransitionAccount}`
  const diagnosticRows = data.diagnostics?.rows ?? []
  const rawDiagnostics = data.diagnostics?.raw ?? {}
  return (
    <section aria-labelledby="system-heading">
      <div className="page-heading"><div><p className="section-kicker">SYSTEM STATUS</p><h2 id="system-heading">시스템 상태</h2><p className="heading-help">시장 연결, 시간, 저장공간, 자동 재연결 상태를 확인합니다.</p></div><span className="page-note">로그인 정보 전송 {data.system.auth_headers ? '감지됨' : '0건'}</span></div>
      <section className="system-summary-grid">
        <article className="panel"><span>시장데이터</span><b className={healthy ? 'positive' : 'warning'}>{marketStatusTitle}</b><small>{data.status.mode === 'DEMO_FIXTURE' ? '오프라인 DEMO · LIVE 아님' : data.status.venue}</small></article>
        <article className="panel"><span>감시 / 정밀 분석</span><b>{data.status.wide_symbols} / {data.status.deep_symbols}종목</b><small>거래대금 핵심과 상승·하락 변동 기회를 함께 분석</small></article>
        <article className="panel"><span>실제 호가 / 체결 지연 p95</span><b>{bookLag.toFixed(0)} / {tradeLag.toFixed(0)}ms</b><small>신규 진입은 실제 호가 지연으로 안전 판단</small></article>
        <article className="panel"><span>현재 시각 KST</span><b title={lastUpdateMs ? formatKstDateTime(lastUpdateMs) : undefined}>{lastUpdateMs ? formatKstTime(lastUpdateMs) : '대기 중'}</b><small>{connected ? `실시간 연결 · 서버 ${clockDeltaMs === null ? '동기 확인 중' : `${clockDeltaMs.toFixed(0)}ms 차이`} · ${venueClockText}` : '자동 재연결 중'}</small></article>
        <article className="panel"><span>비정상 재연결 / 누락</span><b>{reconnects} / {gaps}건</b><small>정상 연결 교체 {plannedRotations}회 · 누락 시 신규 PAPER 진입 잠금</small></article>
        <article className="panel"><span>저장소</span><b className={storageAllowed ? '' : 'warning'}>{storageAllowed ? storage.includes('SQLite') ? '정상 연결' : storage : '신규 진입 잠금'}</b><small>{storageAllowed ? `${Number(data.system.disk_free_mb ?? 0).toFixed(0)}MB 여유 · 불변 원장` : String(data.system.storage_lock_reason ?? '디스크 압박')}</small></article>
        <article className="panel"><span>마지막 시작 복구</span><b className={startupRecovery.healthy ? 'positive' : 'warning'}>{startupRecovery.title}</b><small>{startupRecovery.detail}</small></article>
        <article className="panel"><span>마지막 PAPER 상태</span><b>{paperTransitionTitle}</b><small>{paperTransitionDetail}</small></article>
      </section>
      <section className="panel endpoint-panel"><h3>연결 진실성</h3><p>오프라인 DEMO는 LIVE로 표시하지 않습니다. LIVE 표시는 공개 REST 메타데이터와 첫 sequence-valid WebSocket 이벤트가 모두 확인된 뒤에만 가능합니다.</p><div className="health-row"><span>시장데이터 {marketConnected ? '검증됨' : '미검증'}</span><span>시장 처리 {consumerHealthy ? '정상' : '안전대기'}</span><span>실행 PAPER 전용</span><span>로그인·API 키 불필요</span></div></section>
      <details className="panel advanced-details system-diagnostics"><summary>고급 진단 보기</summary>
        {diagnosticRows.length ? <div className="diagnostic-grid">{diagnosticRows.map((row) => <div key={row.key} data-severity={row.severity}><span>{row.label_ko}</span><b title={row.key === 'release_commit' ? String(row.value) : undefined}>{readableDiagnostic(row)}</b><small>{row.group} · {severityLabel(row.severity)} · {row.user_visible ? '기본 상태' : '전문가'}</small></div>)}</div> : <p role="status">Backend 진단 행을 불러오는 중입니다.</p>}
        <details className="diagnostic-raw"><summary>원시 JSON 보기</summary><pre>{JSON.stringify(rawDiagnostics, null, 2)}</pre></details>
      </details>
    </section>
  )
}
