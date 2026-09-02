// LIVE·REPLAY·종료검토에서 같은 PAPER 포지션 집중 3열 구조를 제공한다.
import { PriceChart, type ChartOverlay } from './PriceChart'
import { useState } from 'react'
import { costProfileLabel, formatPrice, formatQuantity, formatRatio, formatUsdt, paperAccountLabel } from '../format'
import { trailingStateLabel } from '../trailingPresentation'
import type { ChartData, FocusPosition, HistoryRow, ReplayFocusFrame } from '../types'

type Props = {
  mode: 'LIVE' | 'REPLAY' | 'CLOSED_REVIEW'
  position: FocusPosition
  chart: ChartData
  overlay?: ChartOverlay | null
  history?: HistoryRow[]
  replayMilestones?: ReplayFocusFrame['markers']
}

function PlanRail({ position }: { position: FocusPosition }) {
  const priceRationales = [position.stop_rationale_ko, position.take_profit_1_rationale_ko, position.take_profit_2_rationale_ko]
  const hasPriceRationale = priceRationales.some(Boolean)
  const runnerExplanation = position.management_policy?.includes('TP1_ATR_CHANDELIER_RUNNER')
    ? '1차 익절 뒤 남은 물량은 완성봉 ATR 추적선으로 수익을 보호하며 손절선은 넓히지 않습니다.'
    : position.management_policy?.includes('TP1_PATH_STRUCTURE_RUNNER')
      ? '1차 익절 뒤 남은 물량은 진입 전 실제 눌림폭만큼 추적하며 손절선은 넓히지 않습니다.'
      : position.management_policy?.includes('FIXED_SECOND_TARGET')
        ? '1차 익절 뒤 남은 물량은 진입 전에 확정한 2차 구조 가격에서 정리합니다.'
        : ''
  return <aside className="focus-plan" aria-label="진입 계획">
    <div className="focus-rail-title"><span>{position.side === 'LONG' ? '상승 방향' : '하락 방향'} · 설정 {formatRatio(position.selected_leverage, '배')} · 실제 노출 {formatRatio(position.effective_leverage, '배')}</span><b>{position.symbol}</b><small>{position.strategy_display_name_ko} · {costProfileLabel(position.profile)} · {paperAccountLabel(position.account_id)}</small></div>
    <dl>
      <div><dt>실제 진입</dt><dd>{formatPrice(position.actual_entry)}</dd></div><div><dt>초기 손절</dt><dd>{formatPrice(position.initial_stop)}</dd></div><div><dt>현재 손절</dt><dd>{formatPrice(position.current_stop)}<small>{position.current_stop === position.initial_stop ? '변경 없음' : '진입 뒤 조정'}</small></dd></div><div><dt>1차 목표</dt><dd>{formatPrice(position.take_profit_1)}</dd></div><div><dt>2차 목표</dt><dd>{position.take_profit_2 ? formatPrice(position.take_profit_2) : '—'}</dd></div><div><dt>추적 익절</dt><dd>{trailingStateLabel(position.trailing)}<small>{position.trailing?.current_trail ? `보호선 ${formatPrice(position.trailing.current_trail)}` : '활성화 전에는 기존 손절 유지'}</small></dd></div><div><dt>계획 손실</dt><dd>{formatUsdt(position.maximum_planned_loss_usdt)}</dd></div><div><dt>보유 / 남은</dt><dd>{formatQuantity(position.original_quantity)} / {formatQuantity(position.remaining_quantity)}</dd></div><div><dt>명목금액</dt><dd>{formatUsdt(position.notional_usdt)}</dd></div><div><dt>PAPER 증거금</dt><dd>{formatUsdt(position.margin_used_usdt)}</dd></div>
    </dl>
    {hasPriceRationale ? <details className="focus-price-rationale"><summary>이 가격을 정한 이유</summary><p><b>손절</b>{position.stop_rationale_ko}</p><p><b>1차 익절</b>{position.take_profit_1_rationale_ko}</p><p><b>2차 익절</b>{position.take_profit_2_rationale_ko}</p>{runnerExplanation ? <p><b>남은 물량</b>{runnerExplanation}</p> : null}{position.reference_timeframes_ko?.length ? <p><b>확인 구간</b>{position.reference_timeframes_ko.join(' · ')}</p> : null}</details> : null}
    <details><summary>계획 상세</summary><p>위험예산 {formatUsdt(position.risk_budget_usdt)}</p><p>남은 계획손실 {formatUsdt(position.remaining_planned_loss_usdt)}</p><p>{position.exit_style}</p></details>
  </aside>
}

function PnlRail({ position, mode }: { position: FocusPosition; mode: Props['mode'] }) {
  const net = Number(position.net_pnl_usdt)
  const totalCost = Number(position.entry_fee_usdt) + Number(position.realized_exit_fees_usdt) + Number(position.estimated_exit_fee_usdt) + Number(position.slippage_usdt)
  return <aside className="focus-pnl" aria-label="현재 PAPER 손익">
    <div className="focus-rail-title"><span>{mode === 'LIVE' ? '순 평가손익' : '해당 시점 순손익'}</span><b className={net > 0 ? 'positive' : net < 0 ? 'negative' : ''}>{formatUsdt(position.net_pnl_usdt, { signed: true })}</b><small>비용 후 PAPER 평가</small></div>
    <dl><div><dt>현재가</dt><dd>{formatPrice(position.current_mark)}</dd></div><div><dt>총손익</dt><dd>{formatUsdt(position.gross_pnl_usdt, { signed: true })}</dd></div><div><dt>비용</dt><dd>{totalCost ? formatUsdt(-totalCost) : '—'}</dd></div><div><dt>진입 수수료</dt><dd>{formatUsdt(position.entry_fee_usdt)}</dd></div><div><dt>종료 수수료</dt><dd>{formatUsdt(position.realized_exit_fees_usdt)}</dd></div><div><dt>예상 종료비</dt><dd>{formatUsdt(position.estimated_exit_fee_usdt)}</dd></div><div><dt>슬리피지</dt><dd>{formatUsdt(position.slippage_usdt)}</dd></div><div><dt>증거금 수익률</dt><dd>{formatRatio(position.return_on_margin_pct, '%')}</dd></div><div><dt>전략계좌 자산</dt><dd>{formatUsdt(position.account_current_equity_usdt, { equity: true })}</dd></div><div><dt>남은 계획손실</dt><dd>{formatUsdt(position.remaining_planned_loss_usdt)}</dd></div><div><dt>보유시간</dt><dd>{position.elapsed_seconds}초</dd></div><div><dt>단계</dt><dd>{position.stage_ko}</dd></div><div><dt>데이터</dt><dd>{position.data_health}</dd></div>{position.funding_usdt !== undefined ? <div><dt>펀딩</dt><dd>{formatUsdt(position.funding_usdt)}</dd></div> : null}</dl>
    <p className="focus-management">{position.management_reason_ko}</p>
    {mode !== 'LIVE' ? <p className="focus-mode-label">{mode === 'REPLAY' ? '저장 이벤트 재생 중' : '종료 결과 검토'}</p> : null}
  </aside>
}

export function PositionFocusWorkspace({ mode, position, chart, overlay = null, history = [], replayMilestones = [] }: Props) {
  const [sheet, setSheet] = useState<'PLAN' | 'PNL' | null>(null)
  return <div className="focus-stack"><div className="focus-mobile-metrics"><span>순손익 <b>{formatUsdt(position.net_pnl_usdt, { signed: true })}</b></span><span>현재가 <b>{formatPrice(position.current_mark)}</b></span><span>{position.stage_ko}</span></div><div className="focus-sheet-buttons"><button type="button" onClick={() => setSheet('PLAN')}>계획</button><button type="button" onClick={() => setSheet('PNL')}>손익 상세</button></div><div className="focus-grid"><PlanRail position={position} /><PriceChart chart={chart} overlay={overlay} history={history} replayMilestones={replayMilestones} replay={mode !== 'LIVE'} compact /><PnlRail position={position} mode={mode} /></div>{sheet ? <div className="focus-detail-layer" role="dialog" aria-label={sheet === 'PLAN' ? '진입 계획 상세' : 'PAPER 손익 상세'}><button type="button" className="drawer-backdrop" aria-label="상세 닫기" onClick={() => setSheet(null)} />{sheet === 'PLAN' ? <PlanRail position={position} /> : <PnlRail position={position} mode={mode} />}<button type="button" className="focus-sheet-close" onClick={() => setSheet(null)}>닫기</button></div> : null}</div>
}
