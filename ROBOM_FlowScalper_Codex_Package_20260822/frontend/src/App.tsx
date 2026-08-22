// PAPER 상태를 영구 표시하는 첫 대시보드 수직 슬라이스를 렌더링한다.
import { useEffect, useState } from 'react'

type SystemStatus = {
  mode: 'FIXTURE_OFFLINE' | 'LIVE_SHADOW_PAPER' | 'REPLAY'
  market_data_state: 'LIVE' | 'RECONNECTING' | 'STALE' | 'DISCONNECTED' | 'FIXTURE'
  execution_state: 'PAPER'
  venue: 'BINANCE_USDM' | 'BYBIT_LINEAR' | 'FIXTURE'
  run_id: string
  starting_equity_usdt: number
  current_equity_usdt: number
  real_orders_enabled: false
  auth_required: false
  wide_symbols: number
  deep_symbols: number
}

const offlineStatus: SystemStatus = {
  mode: 'FIXTURE_OFFLINE',
  market_data_state: 'FIXTURE',
  execution_state: 'PAPER',
  venue: 'FIXTURE',
  run_id: 'loading',
  starting_equity_usdt: 1000,
  current_equity_usdt: 1000,
  real_orders_enabled: false,
  auth_required: false,
  wide_symbols: 0,
  deep_symbols: 0,
}

export default function App() {
  const [status, setStatus] = useState<SystemStatus>(offlineStatus)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/status', { signal: controller.signal })
      .then((response) => response.json() as Promise<SystemStatus>)
      .then(setStatus)
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

  const isLive = status.market_data_state === 'LIVE'

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">ROBOM RESEARCH TERMINAL</p>
          <h1>FlowScalper</h1>
        </div>
        <div className="badges" aria-label="운영 상태">
          <span className={isLive ? 'badge live' : 'badge fixture'}>
            {isLive ? 'LIVE DATA' : 'OFFLINE FIXTURE'}
          </span>
          <span className="badge paper">PAPER</span>
          <span className="badge disabled">실제 주문 없음</span>
        </div>
      </header>

      <section className="safety" aria-label="안전 상태">
        <strong>실시간 시장데이터</strong>
        <span>{isLive ? '검증됨' : '연결 아님 · 오프라인 시뮬레이션'}</span>
        <strong>페이퍼 계좌 전용</strong>
        <span>로그인 / API 키 필요 없음</span>
      </section>

      <section className="metrics" aria-label="계좌 요약">
        <article><span>현재 자산</span><b>{status.current_equity_usdt.toFixed(2)} USDT</b></article>
        <article><span>시작 자산</span><b>{status.starting_equity_usdt.toFixed(2)} USDT</b></article>
        <article><span>시장 공급자</span><b>{status.venue}</b></article>
        <article><span>Run</span><b>{status.run_id}</b></article>
      </section>

      <section className="workspace">
        <aside className="panel scanner">
          <div className="panel-title"><h2>유니버스 스캐너</h2><span>{status.wide_symbols} 종목</span></div>
          {['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'].map((symbol, index) => (
            <div className="scanner-row" key={symbol}>
              <span>{String(index + 1).padStart(2, '0')}</span><strong>{symbol}</strong><em>CALIBRATING</em>
            </div>
          ))}
        </aside>
        <section className="panel chart">
          <div className="panel-title"><h2>BTCUSDT · 미세구조 차트</h2><span>1초</span></div>
          <div className="chart-grid" aria-label="fixture 차트 자리표시자">
            <div className="price-line entry">진입 계획</div>
            <div className="price-line tp">TP</div>
            <div className="price-line sl">SL</div>
          </div>
        </section>
        <aside className="panel trade">
          <div className="panel-title"><h2>현재 페이퍼 거래</h2><span>없음</span></div>
          <div className="empty-state"><b>후보를 관찰 중입니다</b><p>확률을 만들지 않습니다. 충분한 데이터 전에는 CALIBRATING으로 표시합니다.</p></div>
        </aside>
      </section>
    </main>
  )
}

