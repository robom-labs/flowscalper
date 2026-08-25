// 초보자용 연결 상태와 접이식 원시 진단을 분리해 시스템 진실성을 표시한다.
import type { DashboardData } from '../types'
import { formatKstDateTime, formatKstTime } from '../time'

type Props = { data: DashboardData; connected: boolean; lastUpdateMs: number | null }

const diagnosticLabels: Record<string, string> = {
  api_host: '로컬 API 주소',
  public_endpoint_family: '공개시장 엔드포인트',
  auth_headers: '인증 헤더',
  connection_state: '연결 상태',
  event_count: '처리 이벤트',
  reconnects: '전체 재연결',
  unplanned_reconnects: '비정상 재연결',
  sequence_gaps: '시퀀스 누락',
  resyncs: '재동기화',
  dropped_events: '버린 이벤트',
  queue_depth: '현재 큐',
  queue_capacity: '큐 용량',
  lag_p50_ms: '실제 호가 지연 p50',
  lag_p95_ms: '실제 호가 지연 p95',
  trade_lag_p95_ms: '체결 흐름 지연 p95',
  wide_lag_p95_ms: '종목 스캐너 갱신 지연 p95 · 진입판정 아님',
  stale_trade_events: '늦어서 전략에서 제외한 체결',
  stale_trade_symbols: '체결 흐름 복구 대기 종목',
  venue_clock_offset_ms: '거래소 시각 보정 ms',
  venue_clock_rtt_ms: '시각 확인 왕복 ms',
  clock_sync_status: '거래소 시각 동기화',
  planned_rotations: '계획 회전',
  entry_locked: '신규 진입 잠금',
  last_error: '최근 오류',
  storage: '저장소',
  retention_deep_book_days: '호가 보존일',
  retention_feature_days: '특징 보존일',
  trade_windows_retained: '거래 구간 보존',
  disk_pressure_entry_lock: '디스크 압박 잠금',
  app_version: '앱 버전',
  runtime_ready: '시작 대기 상태',
  process_cpu_percent: '프로세스 CPU %',
  process_memory_mb: '프로세스 메모리 MB',
  process_memory_source: '메모리 측정 기준',
  process_threads: '프로세스 thread',
  process_uptime_seconds: '프로세스 실행시간 초',
  disk_total_mb: '디스크 전체 MB',
  disk_used_mb: '디스크 사용 MB',
  disk_free_mb: '디스크 여유 MB',
  disk_free_ratio: '디스크 여유 비율',
  storage_entry_allowed: '저장소 신규진입 허용',
  storage_guard_enabled: '저장소 보호 활성',
  storage_free_bytes: '보호기준 여유 byte',
  storage_free_ratio: '보호기준 여유 비율',
  storage_lock_reason: '저장소 잠금 사유',
  persistence_fault_count: '원장 저장 실패',
  persistence_last_error: '최근 원장 오류',
  persistence_buffer_dropped: '저장 buffer 유실',
  event_memory_count: '메모리 이벤트 수',
  event_memory_limit: '메모리 이벤트 상한',
  market_persistence_buffer: '시장 저장 대기',
  candle_persistence_buffer: '캔들 저장 대기',
  critical_lag_threshold_ms: '지연 잠금 기준 ms',
  critical_lag_event_count: '실제 호가 지연 기준 초과 이벤트',
  critical_lag_incident_count: '실제 호가 지연 사건',
  critical_lag_last_started_ts_ms: '최근 지연 사건 시작시각 ms',
  critical_lag_last_recovered_ts_ms: '최근 지연 사건 복구시각 ms',
  critical_lag_last_duration_ms: '최근 지연 사건 지속 ms',
  critical_lag_max_duration_ms: '최장 지연 사건 지속 ms',
  critical_lag_active: '실제 호가 지연 신규진입 잠금',
  event_gap_last_ms: '최근 이벤트 수신 간격 ms',
  event_gap_max_ms: '최대 이벤트 수신 공백 ms',
  event_gap_over_500ms_count: '500ms 초과 수신 공백',
  event_gap_last_over_500ms_ts_ms: '최근 500ms 초과 공백시각 ms',
  persistence_flush_count: '시장 저장 완료 횟수',
  persistence_flush_last_ms: '최근 시장 저장 소요 ms',
  persistence_flush_max_ms: '최대 시장 저장 소요 ms',
  persistence_flush_last_completed_ts_ms: '최근 시장 저장 완료시각 ms',
  persistence_flush_max_ts_ms: '최대 시장 저장 발생시각 ms',
  persistence_flush_slow_count: '2초 이상 시장 저장 횟수',
  persistence_flush_last_slow_ts_ms: '최근 2초 이상 저장시각 ms',
  server_time_ms: '서버 시각 ms',
  display_timezone: '표시 시간대',
}

function readable(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (value === null || value === '') return '없음'
  return String(value)
}

export function SystemPage({ data, connected, lastUpdateMs }: Props) {
  const healthy = data.status.market_data_state === 'LIVE' || data.status.market_data_state === 'FIXTURE'
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
  return (
    <section aria-labelledby="system-heading">
      <div className="page-heading"><div><p className="section-kicker">SYSTEM STATUS</p><h2 id="system-heading">시스템 상태</h2><p className="heading-help">시장 연결, 시간, 저장공간, 자동 재연결 상태를 확인합니다.</p></div><span className="page-note">로그인 정보 전송 {data.system.auth_headers ? '감지됨' : '0건'}</span></div>
      <section className="system-summary-grid">
        <article className="panel"><span>시장데이터</span><b className={healthy ? 'positive' : 'warning'}>{healthy ? '정상' : data.status.mode === 'READY' ? '시작 대기' : '재연결 확인 필요'}</b><small>{data.status.mode === 'DEMO_FIXTURE' ? '오프라인 DEMO · LIVE 아님' : data.status.venue}</small></article>
        <article className="panel"><span>감시 / 정밀 분석</span><b>{data.status.wide_symbols} / {data.status.deep_symbols}종목</b><small>넓게 감시한 뒤 상위 종목을 정밀 분석</small></article>
        <article className="panel"><span>실제 호가 / 체결 지연 p95</span><b>{bookLag.toFixed(0)} / {tradeLag.toFixed(0)}ms</b><small>신규 진입은 실제 호가 지연으로 안전 판단</small></article>
        <article className="panel"><span>현재 시각 KST</span><b title={lastUpdateMs ? formatKstDateTime(lastUpdateMs) : undefined}>{lastUpdateMs ? formatKstTime(lastUpdateMs) : '대기 중'}</b><small>{connected ? `실시간 연결 · 서버 ${clockDeltaMs === null ? '동기 확인 중' : `${clockDeltaMs.toFixed(0)}ms 차이`} · ${venueClockText}` : '자동 재연결 중'}</small></article>
        <article className="panel"><span>비정상 재연결 / 누락</span><b>{reconnects} / {gaps}건</b><small>정상 연결 교체 {plannedRotations}회 · 누락 시 신규 PAPER 진입 잠금</small></article>
        <article className="panel"><span>저장소</span><b className={storageAllowed ? '' : 'warning'}>{storageAllowed ? storage.includes('SQLite') ? '정상 연결' : storage : '신규 진입 잠금'}</b><small>{storageAllowed ? `${Number(data.system.disk_free_mb ?? 0).toFixed(0)}MB 여유 · 불변 원장` : String(data.system.storage_lock_reason ?? '디스크 압박')}</small></article>
        <article className="panel"><span>실제 주문 경로</span><b className="positive">0</b><small>private API · 인증 · 주문 전송 없음</small></article>
      </section>
      <section className="panel endpoint-panel"><h3>연결 진실성</h3><p>오프라인 DEMO는 LIVE로 표시하지 않습니다. LIVE 표시는 공개 REST 메타데이터와 첫 sequence-valid WebSocket 이벤트가 모두 확인된 뒤에만 가능합니다.</p><div className="health-row"><span>시장데이터 {healthy ? '검증됨' : '미검증'}</span><span>실행 PAPER 전용</span><span>실제 주문 DISABLED</span><span>로그인·API 키 불필요</span></div></section>
      <details className="panel advanced-details system-diagnostics"><summary>고급 진단 보기</summary><div className="diagnostic-grid">{Object.entries(data.system).map(([name, value]) => <div key={name}><span>{diagnosticLabels[name] ?? name.replaceAll('_', ' ')}</span><b>{readable(value)}</b></div>)}</div></details>
    </section>
  )
}
