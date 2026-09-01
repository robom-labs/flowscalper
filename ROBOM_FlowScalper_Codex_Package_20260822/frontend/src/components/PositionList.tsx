// Market와 Trades가 같은 PAPER 포지션 행과 필터를 재사용하도록 제공한다.
import { memo, useMemo, useState } from 'react'
import { costProfileLabel, formatDurationMs, formatPrice, formatQuantity, formatRatio, formatUsdt } from '../format'
import { strategyLabel } from '../strategyPresentation'
import { trailingSummary } from '../trailingPresentation'
import type { LeaguePosition, StrategySummaryRow } from '../types'

type ProfileFilter = 'BASE' | 'STRESS' | 'ALL'

const PositionRow = memo(function PositionRow({ position, strategy }: { position: LeaguePosition; strategy: StrategySummaryRow | undefined }) {
  const pnl = Number(position.net_pnl)
  return (
    <tr>
      <td data-label="전략"><strong>{strategyLabel(strategy, position.strategy_id)}</strong><small>{costProfileLabel(position.profile)} 가상계좌</small></td>
      <td data-label="거래"><strong>{position.symbol}</strong><small>{position.side === 'LONG' ? '상승 방향' : '하락 방향'}</small></td>
      <td data-label="현재 결과" className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}><strong>{formatUsdt(position.net_pnl, { signed: true })}</strong><small>가격 손익 {formatUsdt(position.gross_pnl, { signed: true })} · 비용 {formatUsdt(Number(position.fees) + Number(position.slippage))}</small></td>
      <td data-label="가격"><strong>진입 {formatPrice(position.actual_entry)}</strong><small>현재 {formatPrice(position.current_mark)}</small></td>
      <td data-label="보호 가격"><strong>1차 {formatPrice(position.TP1)} · 2차 {formatPrice(position.TP2)}</strong><small>손절 {formatPrice(position.current_stop)}</small></td>
      <td data-label="진행 상태"><strong>{formatDurationMs(position.elapsed_seconds * 1_000)} 보유</strong><small>{position.management_reason}</small><small>{trailingSummary(position.trailing)}</small><details className="row-advanced-details"><summary>수량·위험 자세히</summary><span>수량 {formatQuantity(position.original_quantity)} · 남음 {formatQuantity(position.remaining_quantity)}</span><span>설정 {formatRatio(position.selected_leverage, '배')} · 실제 노출 {formatRatio(position.effective_leverage, 'x')}</span><span>거래금액 {formatUsdt(position.notional)} · 증거금 {formatUsdt(position.margin_used_usdt)}</span><span>최초 손절 {formatPrice(position.initial_stop)}</span></details></td>
    </tr>
  )
})

type Props = {
  positions: LeaguePosition[]
  strategies: StrategySummaryRow[]
  compact?: boolean
  showFilters?: boolean
}

export function PositionList({ positions, strategies, compact = false, showFilters = true }: Props) {
  const [profile, setProfile] = useState<ProfileFilter>('BASE')
  const [strategyId, setStrategyId] = useState('ALL')
  const [symbol, setSymbol] = useState('ALL')
  const [side, setSide] = useState('ALL')
  const symbols = useMemo(() => [...new Set(positions.map((position) => position.symbol))].sort(), [positions])
  const filtered = useMemo(() => positions.filter((position) => (
    (profile === 'ALL' || position.profile === profile)
    && (strategyId === 'ALL' || position.strategy_id === strategyId)
    && (symbol === 'ALL' || position.symbol === symbol)
    && (side === 'ALL' || position.side === side)
  )), [positions, profile, side, strategyId, symbol])

  return (
    <section className={compact ? 'position-list compact' : 'position-list'} aria-label="진행 중인 PAPER 거래">
      {showFilters ? <details className="history-filter-details"><summary>보는 범위 바꾸기</summary><div className="position-filters" aria-label="진행 거래 필터">
        <div className="segmented-control" role="group" aria-label="비용 프로필">{(['BASE', 'STRESS', 'ALL'] as const).map((value) => <button type="button" aria-pressed={profile === value} className={profile === value ? 'selected' : ''} key={value} onClick={() => setProfile(value)}>{value === 'ALL' ? '모두' : costProfileLabel(value)}</button>)}</div>
        <label>전략<select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}><option value="ALL">모든 전략</option>{strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.family_label_ko || strategy.short_name} · {strategy.variant_label_ko || strategy.display_name_ko}</option>)}</select></label>
        <label>종목<select value={symbol} onChange={(event) => setSymbol(event.target.value)}><option value="ALL">모든 종목</option>{symbols.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>방향<select value={side} onChange={(event) => setSide(event.target.value)}><option value="ALL">상승·하락 모두</option><option value="LONG">상승 방향</option><option value="SHORT">하락 방향</option></select></label>
      </div></details> : null}
      {filtered.length === 0 ? <div className="empty-state"><b>현재 진행 중인 모의거래가 없습니다.</b><p>진입 조건이 맞으면 전략과 보호 가격이 여기에 표시됩니다.</p></div> : <div className="table-scroll"><table className="league-position-table"><thead><tr><th>전략</th><th>거래</th><th>현재 결과</th><th>가격</th><th>보호 가격</th><th>진행 상태</th></tr></thead><tbody>{filtered.map((position) => <PositionRow key={`${position.account_id}:${position.trade_id}`} position={position} strategy={strategies.find((strategy) => strategy.strategy_id === position.strategy_id)} />)}</tbody></table></div>}
    </section>
  )
}
