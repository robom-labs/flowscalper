// 연결·gap·queue·저장소·버전 진단을 한 화면에 노출한다.
import type { DashboardData } from '../types'

type Props = { data: DashboardData }

export function SystemPage({ data }: Props) {
  return (
    <section aria-labelledby="system-heading">
      <div className="page-heading"><div><p className="section-kicker">DIAGNOSTICS</p><h2 id="system-heading">시스템</h2></div><span className="page-note">자격 증명 전송 {data.system.auth_headers ? '감지됨' : '없음'}</span></div>
      <section className="system-grid">{Object.entries(data.system).map(([name, value]) => <article className="panel" key={name}><span>{name.replaceAll('_', ' ')}</span><b>{String(value)}</b></article>)}</section>
      <section className="panel endpoint-panel"><h3>연결 진실성</h3><p>Fixture 데이터는 LIVE로 표시하지 않습니다. LIVE 표시는 REST 메타데이터와 첫 sequence-valid 공개 WebSocket 이벤트가 모두 확인된 뒤에만 가능합니다.</p><div className="health-row"><span>시장데이터 {data.status.market_data_state}</span><span>실행 {data.status.execution_state}</span><span>실제 주문 {data.status.real_orders_enabled ? '위험' : 'DISABLED'}</span><span>인증 {data.status.auth_required ? '필요' : 'NOT REQUIRED'}</span></div></section>
    </section>
  )
}

