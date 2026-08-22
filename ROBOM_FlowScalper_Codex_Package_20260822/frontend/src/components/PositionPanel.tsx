// 진행 중인 모의거래의 핵심 계획과 손익을 쉬운 말로 표시한다.
import type { CurrentPosition } from '../types'

type Props = { position: CurrentPosition | null; onClose: () => void }

export function PositionPanel({ position, onClose }: Props) {
  if (!position) {
    return <aside className="panel position-panel"><div className="panel-title"><h2>진행 중인 모의거래</h2><span>0건</span></div><div className="empty-state"><b>현재 진행 중인 거래가 없습니다.</b><p>진입 조건이 맞으면 이곳에 계획과 손익이 표시됩니다. 실제 주문은 생성되지 않습니다.</p></div></aside>
  }
  return (
    <aside className="panel position-panel" aria-labelledby="position-title">
      <div className="panel-title"><div><p className="section-kicker">OPEN TRADE</p><h2 id="position-title">{position.symbol} · {position.side === 'LONG' ? '상승 방향' : '하락 방향'}</h2></div><span>모의거래</span></div>
      <dl className="position-grid">
        <div><dt>진입 가격</dt><dd>{position.actual_entry}</dd></div><div><dt>목표 가격</dt><dd>{position.take_profit_1 ?? position.take_profit}{position.take_profit_2 ? ` / ${position.take_profit_2}` : ''}</dd></div>
        <div><dt>손절 가격</dt><dd>{position.initial_stop}</dd></div><div><dt>최대 예상 손실</dt><dd>{position.maximum_planned_loss} USDT</dd></div>
        <div><dt>현재 순손익</dt><dd><strong>{position.net_pnl} USDT</strong></dd></div><div><dt>진행 시간</dt><dd>{position.elapsed_seconds}초</dd></div>
      </dl>
      <p className="management-reason">{position.management_reason}</p>
      {position.expected_resolution ? <p className="resolution">예상 해소시간 {position.expected_resolution}</p> : null}
      <details className="advanced-details">
        <summary>수량·비용·고급 정보 보기</summary>
        <dl className="position-grid">
          <div><dt>전략</dt><dd>{position.strategy}</dd></div><div><dt>수량 / 명목금액</dt><dd>{position.quantity} / {position.notional}</dd></div>
          <div><dt>계획 / 실제 진입</dt><dd>{position.planned_entry} / {position.actual_entry}</dd></div><div><dt>위험예산</dt><dd>{position.risk_budget}</dd></div>
          <div><dt>총손익 / 순손익</dt><dd>{position.gross_pnl} / {position.net_pnl}</dd></div><div><dt>수수료 / 가격차이 비용</dt><dd>{position.fees} / {position.slippage}</dd></div>
        </dl>
        {position.health ? <div className="health-grid">
          {Object.entries(position.health).map(([name, value]) => <div key={name}><span>{name}</span><progress max="1" value={value}>{value}</progress></div>)}
        </div> : <p>실시간 실행은 구조·흐름·유동성·비용 잠금을 서버에서 계속 평가합니다.</p>}
        {position.management_policy ? <ul>{position.management_policy.map((item) => <li key={item}>{item}</li>)}</ul> : null}
      </details>
      <button type="button" className="danger-button" onClick={onClose}>현재 모의거래 종료 요청</button>
    </aside>
  )
}
