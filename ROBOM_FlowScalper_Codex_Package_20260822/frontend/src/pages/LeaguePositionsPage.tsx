// 전략별 진행 중 PAPER 거래를 BASE 기본 필터로 비교한다.
import { memo, useMemo, useState } from 'react'
import { strategyLabel } from '../strategyPresentation'
import type { LeaguePosition, StrategyRow } from '../types'

type ProfileFilter = 'BASE' | 'STRESS' | 'ALL'

const PositionRow = memo(function PositionRow({ position, strategy }: { position: LeaguePosition; strategy: StrategyRow | undefined }) {
  const pnl = Number(position.net_pnl)
  return (
    <tr>
      <td><strong>{strategyLabel(strategy, position.strategy_id)}</strong><small>{position.profile}</small></td>
      <td>{position.symbol}</td>
      <td>{position.side === 'LONG' ? '상승 방향' : '하락 방향'}</td>
      <td>{position.actual_entry}<small>현재 {position.current_mark}</small></td>
      <td>{position.current_stop}<small>최초 {position.initial_stop}</small></td>
      <td>{position.TP1}<small>{position.TP2}</small></td>
      <td>{position.original_quantity}<small>남음 {position.remaining_quantity}</small></td>
      <td>{position.notional}<small>{Number(position.effective_leverage).toFixed(2)}x</small></td>
      <td>{position.gross_pnl}<small>수수료 {position.fees} · 슬리피지 {position.slippage}</small></td>
      <td className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{position.net_pnl}</td>
      <td>{position.elapsed_seconds}초<small>{position.management_reason}</small></td>
    </tr>
  )
})

export function LeaguePositionsPage({ positions, strategies }: { positions: LeaguePosition[]; strategies: StrategyRow[] }) {
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
    <section aria-labelledby="positions-heading">
      <div className="page-heading"><div><p className="section-kicker">OPEN PAPER TRADES</p><h2 id="positions-heading">진행 거래</h2><p className="heading-help">전략별 BASE와 STRESS 가상거래를 분리해서 봅니다. 실제 매수·매도는 없습니다.</p></div><span className="page-note">현재 {filtered.length}건</span></div>
      <div className="position-filters panel" aria-label="진행 거래 필터">
        <div className="segmented-control" aria-label="비용 프로필">{(['BASE', 'STRESS', 'ALL'] as const).map((value) => <button type="button" aria-pressed={profile === value} className={profile === value ? 'selected' : ''} key={value} onClick={() => setProfile(value)}>{value === 'ALL' ? '모두' : value}</button>)}</div>
        <label>전략<select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}><option value="ALL">모든 전략</option>{strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.short_name} · {strategy.display_name_ko}</option>)}</select></label>
        <label>종목<select value={symbol} onChange={(event) => setSymbol(event.target.value)}><option value="ALL">모든 종목</option>{symbols.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label>방향<select value={side} onChange={(event) => setSide(event.target.value)}><option value="ALL">상승·하락 모두</option><option value="LONG">상승 방향</option><option value="SHORT">하락 방향</option></select></label>
      </div>
      <section className="panel wide-panel">
        {filtered.length === 0 ? <div className="empty-state"><b>현재 진행 중인 전략 거래가 없습니다.</b><p>진입 조건이 맞으면 BASE와 STRESS가 각각 별도 항목으로 표시됩니다.</p></div> : <div className="table-scroll"><table className="league-position-table"><thead><tr><th>전략·계좌</th><th>종목</th><th>방향</th><th>진입·현재</th><th>손절</th><th>TP1·TP2</th><th>수량</th><th>명목·레버리지</th><th>총손익·비용</th><th>순손익</th><th>경과·관리</th></tr></thead><tbody>{filtered.map((position) => <PositionRow key={`${position.account_id}:${position.trade_id}`} position={position} strategy={strategies.find((strategy) => strategy.strategy_id === position.strategy_id)} />)}</tbody></table></div>}
      </section>
    </section>
  )
}
