// 후보 점수와 거절 이유를 확률 없이 함께 표시한다.
import type { ScannerRow } from '../types'

type Props = {
  rows: ScannerRow[]
  selectedSymbol: string
  onSelect: (symbol: string) => void
}

const regimeLabels: Record<string, string> = {
  RANGE: '횡보',
  TREND_UP: '상승 추세',
  TREND_DOWN: '하락 추세',
  WARMUP: '학습 중',
}

const statusLabels: Record<string, string> = {
  CALIBRATING: '분석 준비 중',
  REJECTED: '진입 제외',
  OBSERVING: '관찰 중',
  QUALIFIED: '계획 확정',
}

const reasonLabels: Record<string, string> = {
  CALIBRATING: '정밀 데이터 축적 중',
  STALE_OR_DEGRADED_DATA: '데이터 상태가 불안정함',
  WIDE_SPREAD: '매수·매도 가격 차이가 큼',
  COST_FRACTION_TOO_HIGH: '예상 비용 비중이 너무 큼',
  INADEQUATE_NET_REWARD_RISK: '비용 후 손익비가 부족함',
  REGIME_DIRECTION_MISMATCH: '시장 흐름과 진입 방향이 맞지 않음',
  NO_STRUCTURAL_STOP: '구조적인 손절선을 정할 수 없음',
  NO_VIABLE_TARGET: '실행 가능한 목표가를 정할 수 없음',
}

function readableReason(row: ScannerRow) {
  if (!row.reason_codes?.length) return row.reason
  return row.reason_codes.map((code) => reasonLabels[code] ?? code.replaceAll('_', ' ').toLowerCase()).join(' · ')
}

export function ScannerTable({ rows, selectedSymbol, onSelect }: Props) {
  return (
    <section className="panel scanner-panel" aria-labelledby="scanner-title">
      <div className="panel-title">
        <div><p className="section-kicker">DYNAMIC UNIVERSE</p><h2 id="scanner-title">종목 스캐너</h2></div>
        <span>{rows.length}개 관찰</span>
      </div>
      <div className="table-scroll">
        <table className="scanner-table">
          <thead><tr><th>순위</th><th>종목</th><th>레짐</th><th>전략</th><th>점수 / 상태</th><th>순 R:R</th><th>비용</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.symbol} className={`${row.status === 'REJECTED' ? 'rejected ' : ''}${row.symbol === selectedSymbol ? 'selected' : ''}`}>
                <td>{String(row.rank).padStart(2, '0')}</td>
                <td><button type="button" className="symbol-button" aria-pressed={row.symbol === selectedSymbol} onClick={() => onSelect(row.symbol)}><strong>{row.symbol}</strong><small>{row.depth === 'DEEP' ? '정밀 분석' : '넓게 감시'} · {row.side === 'NONE' ? '방향 대기' : row.side}</small></button></td>
                <td><span className="chip">{regimeLabels[row.regime] ?? row.regime}</span></td>
                <td>{row.strategy}</td>
                <td>{row.score ?? statusLabels[row.status] ?? '준비 중'}<small>{readableReason(row)}</small></td>
                <td>{row.net_rr ?? '—'}</td>
                <td>{row.expected_cost_bps.toFixed(1)}bp</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? <p className="empty-copy">공개시장 감시 종목을 기다리는 중입니다.</p> : null}
      </div>
    </section>
  )
}
