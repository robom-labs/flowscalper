// 비전문가가 진입 방향과 준비 상태를 먼저 확인하도록 종목 목록을 단순화한다.
import { useMemo, useState } from 'react'
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

function directionLabel(side: ScannerRow['side']) {
  if (side === 'LONG') return '상승 관찰'
  if (side === 'SHORT') return '하락 관찰'
  return '방향 확인 중'
}

function entryLabel(row: ScannerRow) {
  if (row.status === 'QUALIFIED') return '진입 준비됨'
  if (row.status === 'CALIBRATING') return '분석 중'
  if (row.status === 'REJECTED') return '지금은 대기'
  return '조건 확인 중'
}

export function ScannerTable({ rows, selectedSymbol, onSelect }: Props) {
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null)
  const stableRows = useMemo(
    () => [...rows].sort((left, right) => left.symbol.localeCompare(right.symbol)),
    [rows],
  )

  return (
    <section className="panel scanner-panel" aria-labelledby="scanner-title">
      <div className="panel-title">
        <div><p className="section-kicker">WATCH LIST</p><h2 id="scanner-title">관찰 종목</h2></div>
        <span>{rows.length}개</span>
      </div>
      <p className="panel-help">종목을 누르면 오른쪽 차트가 바뀝니다.</p>
      <div className="scanner-list">
        {stableRows.map((row) => {
          const expanded = expandedSymbol === row.symbol
          return (
            <article
              key={row.symbol}
              className={`scanner-item${row.symbol === selectedSymbol ? ' selected' : ''}`}
            >
              <div className="scanner-main-row">
                <button
                  type="button"
                  className="scanner-symbol-button"
                  aria-pressed={row.symbol === selectedSymbol}
                  onClick={() => onSelect(row.symbol)}
                >
                  <strong>{row.symbol}</strong>
                  <span>{regimeLabels[row.regime] ?? '시장 확인 중'}</span>
                </button>
                <span className={`direction-badge ${row.side.toLowerCase()}`}>{directionLabel(row.side)}</span>
                <span className={`entry-state ${row.status.toLowerCase()}`}>{entryLabel(row)}</span>
                <button
                  type="button"
                  className="scanner-detail-button"
                  aria-expanded={expanded}
                  aria-label={`${row.symbol} 상세 정보 ${expanded ? '닫기' : '보기'}`}
                  onClick={() => setExpandedSymbol(expanded ? null : row.symbol)}
                >
                  {expanded ? '접기' : '상세'}
                </button>
              </div>
              {expanded ? (
                <div className="scanner-detail">
                  <dl>
                    <div><dt>분석 범위</dt><dd>{row.depth === 'DEEP' ? '정밀 분석 중' : '넓게 감시 중'}</dd></div>
                    <div><dt>사용 전략</dt><dd>{row.strategy}</dd></div>
                    <div><dt>비용 후 손익비</dt><dd>{row.net_rr ?? '계산 중'}</dd></div>
                    <div><dt>예상 비용</dt><dd>{row.expected_cost_bps.toFixed(1)}bp</dd></div>
                  </dl>
                  <p>{readableReason(row) || '진입 조건을 확인하고 있습니다.'}</p>
                  <button type="button" className="text-button" onClick={() => onSelect(row.symbol)}>이 종목 차트 보기</button>
                </div>
              ) : null}
            </article>
          )
        })}
        {rows.length === 0 ? <p className="empty-copy">공개시장 감시 종목을 기다리는 중입니다.</p> : null}
      </div>
    </section>
  )
}
