// 현재 PAPER 포지션의 계획·비용·건강·관리근거를 함께 표시한다.
import type { CurrentPosition } from '../types'

type Props = { position: CurrentPosition | null; onClose: () => void }

export function PositionPanel({ position, onClose }: Props) {
  if (!position) {
    return <aside className="panel position-panel"><div className="panel-title"><h2>현재 PAPER 거래</h2><span>포지션 0</span></div><div className="empty-state"><b>열린 페이퍼 포지션이 없습니다</b><p>실제 주문은 어떤 경우에도 생성되지 않습니다.</p></div></aside>
  }
  return (
    <aside className="panel position-panel" aria-labelledby="position-title">
      <div className="panel-title"><div><p className="section-kicker">PAPER POSITION</p><h2 id="position-title">{position.symbol} · {position.side}</h2></div><span>{position.venue}</span></div>
      <dl className="position-grid">
        <div><dt>전략</dt><dd>{position.strategy}</dd></div><div><dt>수량 / 명목</dt><dd>{position.quantity} / {position.notional}</dd></div>
        <div><dt>계획 / 실제 진입</dt><dd>{position.planned_entry} / {position.actual_entry}</dd></div><div><dt>TP1 / TP2 / SL</dt><dd>{position.take_profit_1 ?? position.take_profit} / {position.take_profit_2 ?? '없음'} / {position.initial_stop}</dd></div>
        <div><dt>위험예산 / 최대손실</dt><dd>{position.risk_budget} / {position.maximum_planned_loss}</dd></div><div><dt>보유시간</dt><dd>{position.elapsed_seconds}초</dd></div>
        <div><dt>총 / 순손익</dt><dd>{position.gross_pnl} / <strong>{position.net_pnl} USDT</strong></dd></div><div><dt>수수료 / 슬리피지</dt><dd>{position.fees} / {position.slippage}</dd></div>
      </dl>
      <p className="management-reason">{position.management_reason}</p>
      {position.expected_resolution ? <p className="resolution">예상 해소시간 {position.expected_resolution}</p> : null}
      <details className="advanced-details">
        <summary>고급 진단 보기</summary>
        {position.health ? <div className="health-grid">
          {Object.entries(position.health).map(([name, value]) => <div key={name}><span>{name}</span><progress max="1" value={value}>{value}</progress></div>)}
        </div> : <p>실시간 실행은 구조·흐름·유동성·비용 잠금을 서버에서 계속 평가합니다.</p>}
        {position.management_policy ? <ul>{position.management_policy.map((item) => <li key={item}>{item}</li>)}</ul> : null}
      </details>
      <button type="button" className="danger-button" onClick={onClose}>현재 PAPER 포지션 비상종료</button>
    </aside>
  )
}
