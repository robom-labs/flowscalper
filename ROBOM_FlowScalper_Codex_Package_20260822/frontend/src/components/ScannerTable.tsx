// 후보 점수와 거절 이유를 확률 없이 함께 표시한다.
import type { ScannerRow } from '../types'

type Props = { rows: ScannerRow[] }

export function ScannerTable({ rows }: Props) {
  return (
    <section className="panel scanner-panel" aria-labelledby="scanner-title">
      <div className="panel-title">
        <div><p className="section-kicker">DYNAMIC UNIVERSE</p><h2 id="scanner-title">종목 스캐너</h2></div>
        <span>{rows.length}개 관찰</span>
      </div>
      <div className="table-scroll">
        <table className="scanner-table">
          <thead><tr><th>순위</th><th>종목</th><th>레짐</th><th>전략</th><th>점수</th><th>순 R:R</th><th>비용</th><th>상태 / 이유</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.symbol} className={row.status === 'REJECTED' ? 'rejected' : ''}>
                <td>{String(row.rank).padStart(2, '0')}</td>
                <td><strong>{row.symbol}</strong><small>{row.depth} · {row.side}</small></td>
                <td><span className="chip">{row.regime}</span></td>
                <td>{row.strategy}</td>
                <td>{row.score ?? row.calibration}</td>
                <td>{row.net_rr ?? '—'}</td>
                <td>{row.expected_cost_bps.toFixed(1)}bp</td>
                <td><b>{row.status}</b><small>{row.reason}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? <p className="empty-copy">fixture 스캐너를 불러오는 중입니다.</p> : null}
      </div>
    </section>
  )
}

