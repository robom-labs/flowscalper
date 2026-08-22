// 비용과 실제 PAPER fill을 포함한 변경 불가 거래내역을 표시한다.
import type { HistoryRow } from '../types'

type Props = { rows: HistoryRow[]; onReplay: () => void }

export function HistoryPage({ rows, onReplay }: Props) {
  return (
    <section aria-labelledby="history-heading">
      <div className="page-heading"><div><p className="section-kicker">IMMUTABLE RECORDS</p><h2 id="history-heading">거래내역</h2></div><span className="page-note">fixture 표본과 실제 live-paper Run을 분리 표시합니다.</span></div>
      <section className="panel wide-panel table-scroll">
        <table className="history-table"><thead><tr><th>Run / 거래</th><th>종목</th><th>전략</th><th>진입→종료</th><th>종료사유</th><th>총손익</th><th>비용</th><th>순손익</th><th>보유</th><th>리플레이</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.trade_id}><td><strong>{row.trade_id}</strong><small>{row.run_id}<br />{row.sample_type}</small></td><td>{row.symbol}<small>{row.side}</small></td><td>{row.strategy}<small>{row.profile}</small></td><td>{row.entry} → {row.exit}</td><td>{row.exit_reason}</td><td>{row.gross_pnl}</td><td>수수료 {row.fees}<br />슬리피지 {row.slippage}</td><td className="positive">{row.net_pnl}</td><td>{row.holding_seconds}초</td><td><button type="button" className="table-button" onClick={onReplay}>열기</button></td></tr>)}</tbody>
        </table>
        {rows.length === 0 ? <p className="empty-copy">완료된 PAPER 거래가 없습니다.</p> : null}
      </section>
    </section>
  )
}

