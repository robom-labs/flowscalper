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
  strategy_evaluation_count: '누적 전략 판정 경로',
  qualified_signal_count: '누적 적격 신호',
  venue_clock_offset_ms: '거래소 시각 보정 ms',
  venue_clock_rtt_ms: '시각 확인 왕복 ms',
  clock_sync_status: '거래소 시각 동기화',
  planned_rotations: '계획 회전',
  entry_locked: '신규 진입 잠금',
  last_error: '최근 오류',
  supervisor_running: '시장 관찰 작업 실행',
  consumer_running: '시장 처리 작업 실행',
  consumer_delivery_count: '시장 처리 완료 이벤트',
  consumer_delivery_failure_count: '시장 처리 오류',
  consumer_delivery_drop_count: '시장 처리 오류 누락',
  consumer_recovery_count: '시장 처리 자동 복구',
  consumer_fault_active: '시장 처리 안전잠금',
  consumer_last_delivery_ts_ms: '최근 시장 처리 완료시각 ms',
  consumer_last_failure_ts_ms: '최근 시장 처리 오류시각 ms',
  consumer_last_recovered_ts_ms: '최근 시장 처리 복구시각 ms',
  queue_overload_active: 'queue 과부하 안전잠금',
  queue_overload_incident_count: 'queue 과부하 사건',
  queue_overload_recovery_count: 'queue 과부하 복구',
  queue_overload_drop_count: 'queue 과부하 누락',
  queue_overload_last_started_ts_ms: '최근 queue 과부하 시작시각 ms',
  queue_overload_last_recovered_ts_ms: '최근 queue 과부하 복구시각 ms',
  storage: '저장소',
  retention_deep_book_days: '호가 보존일',
  retention_feature_days: '특징 보존일',
  trade_windows_retained: '거래 구간 보존',
  disk_pressure_entry_lock: '디스크 압박 잠금',
  app_version: '앱 버전',
  release_commit: '실행 릴리스',
  release_isolated: '개발 폴더와 실행본 분리',
  runtime_ready: '시작 대기 상태',
  process_cpu_percent: '프로세스 CPU %',
  process_memory_mb: '현재 프로세스 메모리 RSS MB',
  process_memory_source: '현재 메모리 측정 기준',
  process_memory_peak_mb: '프로세스 최고 메모리 RSS MB',
  process_memory_peak_source: '최고 메모리 측정 기준',
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
  persistence_fault_count: '누적 저장 이상 횟수',
  persistence_fault_active: '현재 저장 이상 활성',
  persistence_fault_recoverable: '저장공간 회복 대기',
  persistence_recovery_count: '저장 자동 복구 횟수',
  persistence_last_recovered_ts_ms: '최근 저장 복구시각 ms',
  persistence_last_recovered_error: '최근 복구된 저장 이상',
  persistence_last_error: '최근 원장 오류',
  persistence_buffer_dropped: '저장 buffer 유실',
  persistence_backlog_peak: '시장 저장 대기 최대 건수',
  persistence_backlog_entry_lock_count: '저장 적체 안전대기 횟수',
  persistence_backlog_entry_lock_events: '저장 적체 진입잠금 기준',
  persistence_backlog_recovery_events: '저장 적체 회복 기준',
  event_memory_count: '메모리 이벤트 수',
  event_memory_limit: '메모리 이벤트 상한',
  market_persistence_buffer: '시장 저장 대기',
  candle_persistence_buffer: '캔들 저장 대기',
  critical_lag_threshold_ms: '지연 잠금 기준 ms',
  critical_lag_event_count: '실제 호가 지연 기준 초과 이벤트',
  quarantined_executable_lag_event_count: '전략 입력에서 격리한 늦은 실제 호가',
  quarantined_executable_lag_last_symbol: '최근 격리 호가 종목',
  quarantined_executable_lag_last_event_type: '최근 격리 호가 종류',
  quarantined_executable_lag_last_ms: '최근 격리 호가 지연 ms',
  quarantined_executable_lag_last_venue_ts_ms: '최근 격리 호가 거래소시각 ms',
  critical_lag_incident_count: '실제 호가 지연 사건',
  critical_lag_last_started_ts_ms: '최근 지연 사건 시작시각 ms',
  critical_lag_last_recovered_ts_ms: '최근 지연 사건 복구시각 ms',
  critical_lag_last_duration_ms: '최근 지연 사건 지속 ms',
  critical_lag_max_duration_ms: '최장 지연 사건 지속 ms',
  critical_lag_active: '실제 호가 지연 신규진입 잠금',
  event_gap_last_ms: '최근 이벤트 수신 간격 ms',
  event_gap_max_ms: '최대 이벤트 수신 공백 ms',
  event_gap_max_ts_ms: '최대 이벤트 수신 공백시각 ms',
  event_gap_over_500ms_count: '500ms 초과 수신 공백',
  event_gap_last_over_500ms_ts_ms: '최근 500ms 초과 공백시각 ms',
  event_loop_lag_last_ms: '로컬 처리루프 최근 지연 ms',
  event_loop_lag_max_ms: '로컬 처리루프 최대 지연 ms',
  event_loop_lag_over_100ms_count: '로컬 처리루프 100ms 초과 횟수',
  event_loop_lag_last_over_100ms_ts_ms: '최근 로컬 처리루프 지연시각 ms',
  event_loop_lag_over_500ms_count: '로컬 처리루프 500ms 초과 횟수',
  event_loop_lag_last_over_500ms_ts_ms: '최근 로컬 처리루프 500ms 초과시각 ms',
  event_loop_lag_last_over_500ms_ms: '최근 로컬 처리루프 500ms 초과값 ms',
  persistence_flush_count: '시장 저장 완료 횟수',
  persistence_flush_last_ms: '최근 시장 저장 소요 ms',
  persistence_flush_max_ms: '최대 시장 저장 소요 ms',
  persistence_flush_last_completed_ts_ms: '최근 시장 저장 완료시각 ms',
  persistence_flush_max_ts_ms: '최대 시장 저장 발생시각 ms',
  persistence_flush_slow_count: '2초 이상 시장 저장 횟수',
  persistence_flush_last_slow_ts_ms: '최근 2초 이상 저장시각 ms',
  persistence_flush_slowest_archive_ms: '최장 저장 중 Parquet 작성 ms',
  persistence_flush_slowest_ledger_ms: '최장 저장 중 원장 통합 커밋 ms',
  persistence_flush_slowest_market_events: '최장 저장의 시장 이벤트 수',
  persistence_flush_slowest_candles: '최장 저장의 candle 수',
  persistence_flush_slowest_archive_batches: '최장 저장의 Parquet 배치 수',
  wal_autocheckpoint_pages: 'COMMIT 자동 checkpoint 설정 · 0은 꺼짐',
  wal_checkpoint_flush_interval: '몇 번 저장마다 분리 checkpoint',
  wal_checkpoint_count: '분리 WAL checkpoint 시도 횟수',
  wal_checkpoint_last_ms: '최근 WAL checkpoint ms',
  wal_checkpoint_max_ms: '최대 WAL checkpoint ms',
  wal_checkpoint_slow_count: '2초 이상 WAL checkpoint 횟수',
  wal_checkpoint_busy_count: '부분 WAL checkpoint 횟수',
  wal_checkpoint_log_frames: '최근 WAL 전체 프레임',
  wal_checkpointed_frames: '최근 WAL 반영 프레임',
  wal_checkpoint_last_completed_ts_ms: '최근 WAL checkpoint 종료시각 ms',
  wal_checkpoint_fault_count: 'WAL checkpoint 오류 횟수',
  wal_checkpoint_last_error: '최근 WAL checkpoint 오류',
  wal_checkpoint_deferred_count: '저장 적체 중 checkpoint 연기 횟수',
  wal_checkpoint_last_wal_bytes: '최근 checkpoint 판단 WAL bytes',
  wal_checkpoint_soft_bytes: '적체 중 checkpoint 실행 기준 bytes',
  startup_storage_init_ms: '부팅 저장소 준비 ms',
  startup_ledger_open_ms: '부팅 SQLite 열기 ms',
  startup_recovery_lookup_ms: '부팅 복구상태 조회 ms',
  startup_runtime_init_ms: '부팅 런타임·통계 준비 ms',
  startup_recovery_restore_ms: '부팅 PAPER 상태복구 ms',
  startup_recovery_transition_id: '마지막 시작 복구 전이 ID',
  startup_recovery_previous_state: '시작 복구 이전 상태',
  startup_recovery_state: '시작 복구 결과',
  startup_recovery_cause_code: '시작 복구 원인 코드',
  startup_recovery_actor: '시작 복구 주체',
  startup_recovery_run_id: '시작 복구 Run',
  startup_recovery_occurred_ts_ms: '시작 복구 발생시각 ms',
  startup_recovery_reversible: '시작 복구 후속 복구 가능',
  last_paper_transition_id: '마지막 PAPER 전환 ID',
  last_paper_transition_previous_state: 'PAPER 이전 상태',
  last_paper_transition_state: '마지막 PAPER 전환 결과',
  last_paper_transition_cause_code: 'PAPER 전환 원인 코드',
  last_paper_transition_actor: 'PAPER 전환 주체',
  last_paper_transition_account_id: 'PAPER 전환 계좌',
  last_paper_transition_symbol: 'PAPER 전환 종목',
  last_paper_transition_occurred_ts_ms: 'PAPER 전환 발생시각 ms',
  last_paper_transition_reversible: 'PAPER 전환 되돌림 가능',
  startup_total_ms: '부팅 전체 준비 ms',
  startup_portfolio_init_ms: '부팅 PAPER 계좌 준비 ms',
  startup_trade_cache_ms: '부팅 과거 거래통계 준비 ms',
  startup_post_init_total_ms: '부팅 런타임 내부 전체 ms',
  dashboard_trade_cache_ready: '과거 거래통계 준비 완료',
  dashboard_trade_cache_loading: '과거 거래통계 불러오는 중',
  dashboard_trade_cache_last_ms: '과거 거래통계 최근 준비 ms',
  dashboard_trade_cache_completed_ts_ms: '과거 거래통계 준비 완료시각 ms',
  server_time_ms: '서버 시각 ms',
  display_timezone: '표시 시간대',
}

function readable(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (value === null || value === '') return '없음'
  return String(value)
}

function readableDiagnostic(name: string, value: string | number | boolean) {
  if (name === 'release_commit' && typeof value === 'string' && value.length === 40) {
    return value.slice(0, 12)
  }
  return readable(value)
}

export function SystemPage({ data, connected, lastUpdateMs }: Props) {
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
  return (
    <section aria-labelledby="system-heading">
      <div className="page-heading"><div><p className="section-kicker">SYSTEM STATUS</p><h2 id="system-heading">시스템 상태</h2><p className="heading-help">시장 연결, 시간, 저장공간, 자동 재연결 상태를 확인합니다.</p></div><span className="page-note">로그인 정보 전송 {data.system.auth_headers ? '감지됨' : '0건'}</span></div>
      <section className="system-summary-grid">
        <article className="panel"><span>시장데이터</span><b className={healthy ? 'positive' : 'warning'}>{marketStatusTitle}</b><small>{data.status.mode === 'DEMO_FIXTURE' ? '오프라인 DEMO · LIVE 아님' : data.status.venue}</small></article>
        <article className="panel"><span>감시 / 정밀 분석</span><b>{data.status.wide_symbols} / {data.status.deep_symbols}종목</b><small>넓게 감시한 뒤 상위 종목을 정밀 분석</small></article>
        <article className="panel"><span>실제 호가 / 체결 지연 p95</span><b>{bookLag.toFixed(0)} / {tradeLag.toFixed(0)}ms</b><small>신규 진입은 실제 호가 지연으로 안전 판단</small></article>
        <article className="panel"><span>현재 시각 KST</span><b title={lastUpdateMs ? formatKstDateTime(lastUpdateMs) : undefined}>{lastUpdateMs ? formatKstTime(lastUpdateMs) : '대기 중'}</b><small>{connected ? `실시간 연결 · 서버 ${clockDeltaMs === null ? '동기 확인 중' : `${clockDeltaMs.toFixed(0)}ms 차이`} · ${venueClockText}` : '자동 재연결 중'}</small></article>
        <article className="panel"><span>비정상 재연결 / 누락</span><b>{reconnects} / {gaps}건</b><small>정상 연결 교체 {plannedRotations}회 · 누락 시 신규 PAPER 진입 잠금</small></article>
        <article className="panel"><span>저장소</span><b className={storageAllowed ? '' : 'warning'}>{storageAllowed ? storage.includes('SQLite') ? '정상 연결' : storage : '신규 진입 잠금'}</b><small>{storageAllowed ? `${Number(data.system.disk_free_mb ?? 0).toFixed(0)}MB 여유 · 불변 원장` : String(data.system.storage_lock_reason ?? '디스크 압박')}</small></article>
        <article className="panel"><span>마지막 시작 복구</span><b className={startupRecovery.healthy ? 'positive' : 'warning'}>{startupRecovery.title}</b><small>{startupRecovery.detail}</small></article>
        <article className="panel"><span>마지막 PAPER 상태</span><b>{paperTransitionTitle}</b><small>{paperTransitionDetail}</small></article>
        <article className="panel"><span>실제 주문 경로</span><b className="positive">0</b><small>private API · 인증 · 주문 전송 없음</small></article>
      </section>
      <section className="panel endpoint-panel"><h3>연결 진실성</h3><p>오프라인 DEMO는 LIVE로 표시하지 않습니다. LIVE 표시는 공개 REST 메타데이터와 첫 sequence-valid WebSocket 이벤트가 모두 확인된 뒤에만 가능합니다.</p><div className="health-row"><span>시장데이터 {marketConnected ? '검증됨' : '미검증'}</span><span>시장 처리 {consumerHealthy ? '정상' : '안전대기'}</span><span>실행 PAPER 전용</span><span>실제 주문 DISABLED</span><span>로그인·API 키 불필요</span></div></section>
      <details className="panel advanced-details system-diagnostics"><summary>고급 진단 보기</summary><div className="diagnostic-grid">{Object.entries(data.system).map(([name, value]) => <div key={name}><span>{diagnosticLabels[name] ?? name.replaceAll('_', ' ')}</span><b title={name === 'release_commit' ? String(value) : undefined}>{readableDiagnostic(name, value)}</b></div>)}</div></details>
    </section>
  )
}
