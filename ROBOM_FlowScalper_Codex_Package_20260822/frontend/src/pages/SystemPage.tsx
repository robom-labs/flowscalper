// 초보자용 연결 상태와 접이식 원시 진단을 분리해 시스템 진실성을 표시한다.
import type { DashboardData } from '../types'

type Props = { data: DashboardData; connected: boolean; lastUpdateMs: number | null }

const diagnosticLabels: Record<string, string> = {
  api_host: '로컬 API 주소',
  public_endpoint_family: '공개시장 엔드포인트',
  auth_headers: '인증 헤더',
  connection_state: '연결 상태',
  event_count: '처리 이벤트',
  reconnects: '재연결',
  sequence_gaps: '시퀀스 누락',
  resyncs: '재동기화',
  dropped_events: '버린 이벤트',
  queue_depth: '현재 큐',
  queue_capacity: '큐 용량',
  lag_p50_ms: '처리 지연 p50',
  lag_p95_ms: '처리 지연 p95',
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
}

function readable(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (value === null || value === '') return '없음'
  return String(value)
}

export function SystemPage({ data, connected, lastUpdateMs }: Props) {
  const healthy = data.status.market_data_state === 'LIVE' || data.status.market_data_state === 'FIXTURE'
  const reconnects = Number(data.system.reconnects ?? 0)
  const gaps = Number(data.system.sequence_gaps ?? 0)
  const storage = String(data.system.storage ?? '확인 중')
  return (
    <section aria-labelledby="system-heading">
      <div className="page-heading"><div><p className="section-kicker">DIAGNOSTICS</p><h2 id="system-heading">시스템</h2><p className="heading-help">기본 화면은 운영 판단만 보여주고, 원시 값은 고급 진단에 분리합니다.</p></div><span className="page-note">자격 증명 전송 {data.system.auth_headers ? '감지됨' : '0건'}</span></div>
      <section className="system-summary-grid">
        <article className="panel"><span>시장데이터</span><b className={healthy ? 'positive' : 'warning'}>{healthy ? '정상' : data.status.mode === 'READY' ? '시작 대기' : '재연결 확인 필요'}</b><small>{data.status.mode === 'DEMO_FIXTURE' ? '오프라인 DEMO · LIVE 아님' : data.status.venue}</small></article>
        <article className="panel"><span>감시 / 정밀 분석</span><b>{data.status.wide_symbols} / {data.status.deep_symbols}종목</b><small>넓게 감시한 뒤 상위 종목을 정밀 분석</small></article>
        <article className="panel"><span>UI 마지막 갱신</span><b>{lastUpdateMs ? new Date(lastUpdateMs).toLocaleTimeString('ko-KR') : '대기 중'}</b><small>{connected ? '실시간 연결됨' : '자동 재연결 중'}</small></article>
        <article className="panel"><span>재연결 / 누락</span><b>{reconnects} / {gaps}건</b><small>누락 시 신규 PAPER 진입 잠금</small></article>
        <article className="panel"><span>저장소</span><b>{storage.includes('SQLite') ? '정상 연결' : storage}</b><small>거래·시장 이벤트 불변 원장</small></article>
        <article className="panel"><span>실제 주문 경로</span><b className="positive">0</b><small>private API · 인증 · 주문 전송 없음</small></article>
      </section>
      <section className="panel endpoint-panel"><h3>연결 진실성</h3><p>오프라인 DEMO는 LIVE로 표시하지 않습니다. LIVE 표시는 공개 REST 메타데이터와 첫 sequence-valid WebSocket 이벤트가 모두 확인된 뒤에만 가능합니다.</p><div className="health-row"><span>시장데이터 {healthy ? '검증됨' : '미검증'}</span><span>실행 PAPER 전용</span><span>실제 주문 DISABLED</span><span>로그인·API 키 불필요</span></div></section>
      <details className="panel advanced-details system-diagnostics"><summary>고급 진단 보기</summary><div className="diagnostic-grid">{Object.entries(data.system).map(([name, value]) => <div key={name}><span>{diagnosticLabels[name] ?? name.replaceAll('_', ' ')}</span><b>{readable(value)}</b></div>)}</div></details>
    </section>
  )
}
