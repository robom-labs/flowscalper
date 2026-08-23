// 고정 행과 별도 drawer로 실시간 종목 목록의 위치와 크기를 안정화한다.
import { memo, useCallback, useState } from 'react'
import { useStableScannerOrder } from '../hooks/useStableScannerOrder'
import type { ScannerRow } from '../types'
import { SideDrawer } from './SideDrawer'

type Props = {
  rows: ScannerRow[]
  venue: string
  selectedSymbol: string
  protectedSymbols?: string[]
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
  return '방향 확인'
}

function entryLabel(row: ScannerRow) {
  if (row.status === 'QUALIFIED') return '진입 준비'
  if (row.status === 'CALIBRATING') return '분석 중'
  if (row.status === 'REJECTED') return '대기'
  return '확인 중'
}

const ScannerRowItem = memo(function ScannerRowItem({
  row,
  venue,
  selected,
  onSelect,
  onDetail,
}: {
  row: ScannerRow
  venue: string
  selected: boolean
  onSelect: (symbol: string) => void
  onDetail: (row: ScannerRow) => void
}) {
  return (
    <article className={`scanner-item${selected ? ' selected' : ''}`} data-row-key={`${venue}:${row.symbol}`}>
      <div className="scanner-main-row">
        <button type="button" className="scanner-symbol-button" aria-pressed={selected} onClick={() => onSelect(row.symbol)}>
          <strong>{row.symbol}</strong>
          <span>{regimeLabels[row.regime] ?? '시장 확인 중'}</span>
        </button>
        <span className={`direction-badge ${row.side.toLowerCase()}`}>{directionLabel(row.side)}</span>
        <span className={`entry-state ${row.status.toLowerCase()}`}>{entryLabel(row)}</span>
        <button type="button" className="scanner-detail-button" aria-label={`${row.symbol} 상세 정보 보기`} onClick={() => onDetail(row)}>상세</button>
      </div>
    </article>
  )
})

export function ScannerTable({ rows, venue, selectedSymbol, protectedSymbols = [], onSelect }: Props) {
  const [rankLocked, setRankLocked] = useState(true)
  const [detailRow, setDetailRow] = useState<ScannerRow | null>(null)
  const stableRows = useStableScannerOrder(rows, rankLocked, selectedSymbol, protectedSymbols)
  const closeDrawer = useCallback(() => setDetailRow(null), [])

  return (
    <section className="panel scanner-panel" aria-labelledby="scanner-title">
      <div className="panel-title scanner-title-row">
        <div><p className="section-kicker">WATCH LIST</p><h2 id="scanner-title">관찰 종목</h2></div>
        <div className="scanner-title-actions">
          <span>{rows.length}개</span>
          <button type="button" aria-pressed={rankLocked} onClick={() => setRankLocked((value) => !value)}>
            {rankLocked ? '순위 고정' : '순위 자동정렬'}
          </button>
        </div>
      </div>
      <p className="panel-help">종목을 누르면 오른쪽 차트가 바뀝니다.</p>
      <div className="scanner-column-head" aria-hidden="true"><span>종목</span><span>방향</span><span>상태</span><span>정보</span></div>
      <div className="scanner-list">
        {stableRows.map((row) => (
          <ScannerRowItem
            key={`${venue}:${row.symbol}`}
            row={row}
            venue={venue}
            selected={row.symbol === selectedSymbol}
            onSelect={onSelect}
            onDetail={setDetailRow}
          />
        ))}
        {rows.length === 0 ? <p className="empty-copy">공개시장 감시 종목을 기다리는 중입니다.</p> : null}
      </div>
      <SideDrawer title={detailRow ? `${detailRow.symbol} 상세` : '종목 상세'} open={detailRow !== null} onClose={closeDrawer} label="종목 상세 정보">
        {detailRow ? <>
          <dl className="drawer-detail-list">
            <div><dt>분석 범위</dt><dd>{detailRow.depth === 'DEEP' ? '정밀 분석 중' : '넓게 감시 중'}</dd></div>
            <div><dt>시장 상태</dt><dd>{regimeLabels[detailRow.regime] ?? detailRow.regime}</dd></div>
            <div><dt>사용 전략</dt><dd>{detailRow.strategy}</dd></div>
            <div><dt>관찰 방향</dt><dd>{directionLabel(detailRow.side)}</dd></div>
            <div><dt>현재 상태</dt><dd>{entryLabel(detailRow)}</dd></div>
            <div><dt>비용 후 손익비</dt><dd>{detailRow.net_rr ?? '계산 중'}</dd></div>
            <div><dt>예상 비용</dt><dd>{detailRow.expected_cost_bps.toFixed(1)}bp</dd></div>
            <div><dt>매수·매도 가격 차이</dt><dd>{detailRow.spread_bps.toFixed(2)}bp</dd></div>
            <div><dt>데이터 상태</dt><dd>{detailRow.data_health}</dd></div>
          </dl>
          <p className="drawer-reason">{readableReason(detailRow) || '진입 조건을 확인하고 있습니다.'}</p>
          <button type="button" className="primary-button full-width" onClick={() => { onSelect(detailRow.symbol); closeDrawer() }}>이 종목 차트 보기</button>
        </> : null}
      </SideDrawer>
    </section>
  )
}
