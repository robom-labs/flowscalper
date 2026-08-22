// 데이터 진실성과 PAPER 전용 상태를 모든 화면 상단에 영구 표시한다.
import type { DashboardData } from '../types'

type Props = { data: DashboardData; connected: boolean }

export function SafetyHeader({ data, connected }: Props) {
  const { status } = data
  const live = status.market_data_state === 'LIVE'
  const publicMode = status.mode === 'LIVE_SHADOW_PAPER'
  const sourceLabel = live
    ? `LIVE DATA · ${status.venue}`
    : publicMode
      ? `${status.market_data_state} · ${status.venue} · LIVE 아님`
      : 'OFFLINE FIXTURE · LIVE 아님'
  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">ROBOM PAPER RESEARCH TERMINAL</p>
          <h1>FlowScalper</h1>
        </div>
        <div className="badges" aria-label="운영 상태">
          <span className={live ? 'badge live' : 'badge fixture'}>{sourceLabel}</span>
          <span className="badge paper">PAPER</span>
          <span className="badge disabled">실제 주문 없음</span>
          <span className={connected ? 'badge socket-on' : 'badge socket-off'}>
            UI {connected ? '실시간 연결' : '재연결 중'}
          </span>
        </div>
      </header>
      <section className="safety" aria-label="영구 안전 상태">
        <span><b>시장데이터</b> {live ? '검증된 공개 이벤트' : publicMode ? '검증 대기·신규 진입 차단' : '오프라인 시뮬레이션'}</span>
        <span><b>실행</b> 내부 PAPER만</span>
        <span><b>시작자산</b> {status.starting_equity_usdt.toFixed(2)} USDT</span>
        <span><b>로그인 / API 키</b> 필요 없음</span>
        <span><b>Run</b> {status.run_id}</span>
      </section>
    </>
  )
}
