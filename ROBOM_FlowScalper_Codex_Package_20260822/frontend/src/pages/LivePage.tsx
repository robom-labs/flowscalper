// 비전문가가 자동 관찰 상태와 독립 전략·공동계좌 결과를 혼동 없이 보는 홈이다.
import { modeLabels, orderedStrategies } from '../strategyPresentation'
import { formatKstTime } from '../time'
import type { ControlOperation, DashboardData, PageId } from '../types'

type Props = {
  data: DashboardData
  operation: ControlOperation | null
  busyAction: string | null
  connectionError: string
  requestError: string
  onPauseToggle: () => void
  onStartLive: () => void
  onStartDemo: () => void
  onCancel: () => void
  onRetry: () => void
  onNavigate: (page: PageId) => void
}

const terminalStates = new Set(['COMPLETED', 'FAILED_RETRYABLE', 'FAILED_BLOCKED', 'CANCELLED'])

function sum(rows: { current_equity_usdt: string; starting_equity_usdt: string }[], field: 'current' | 'pnl') {
  return rows.reduce((total, row) => total + (field === 'current' ? Number(row.current_equity_usdt) : Number(row.current_equity_usdt) - Number(row.starting_equity_usdt)), 0)
}

export function LivePage({ data, operation, busyAction, connectionError, requestError, onPauseToggle, onStartLive, onStartDemo, onCancel, onRetry, onNavigate }: Props) {
  const ready = data.status.mode === 'READY'
  const demo = data.status.mode === 'DEMO_FIXTURE'
  const activeOperation = operation && !terminalStates.has(operation.state)
  const baseAccounts = data.league_accounts.filter((account) => account.profile === 'BASE')
  const ordered = orderedStrategies(data.strategies)
  const leadingAccount = [...baseAccounts].sort((left, right) => {
    const equity = Number(right.current_equity_usdt) - Number(left.current_equity_usdt)
    if (equity !== 0) return equity
    return ordered.findIndex((strategy) => strategy.strategy_id === left.strategy_id) - ordered.findIndex((strategy) => strategy.strategy_id === right.strategy_id)
  })[0]
  const leadingStrategy = ordered.find((strategy) => strategy.strategy_id === leadingAccount?.strategy_id)
  const sharedPnl = data.status.current_equity_usdt - data.status.starting_equity_usdt
  return (
    <section aria-labelledby="live-heading">
      <div className="page-heading">
        <div><p className="section-kicker">HOME</p><h2 id="live-heading">자동 관찰 홈</h2><p className="heading-help">공개시장 데이터를 보며 모의로만 거래합니다. 실제 돈은 움직이지 않습니다.</p></div>
        <div className="control-row home-controls">
          {activeOperation ? <button type="button" className="secondary-button" onClick={onCancel} disabled={operation.state === 'CANCELLING'}>{operation.state === 'CANCELLING' ? '취소 중' : '연결 취소'}</button> : ready || demo ? <button type="button" className="primary-button" onClick={onStartLive} disabled={busyAction !== null}>{demo ? '실제 공개시장으로 시작' : '자동 관찰 시작'}</button> : <button type="button" className={data.paused ? 'primary-button' : 'secondary-button'} onClick={onPauseToggle}>{data.paused ? '자동 관찰 계속하기' : '새 진입 잠시 멈추기'}</button>}
          {!activeOperation && ready ? <button type="button" className="secondary-button" onClick={onStartDemo} disabled={busyAction !== null}>샘플 화면 보기</button> : null}
        </div>
      </div>
      {connectionError ? <p className="connection-error" role="alert"><b>연결 상태</b> {connectionError}</p> : null}
      {requestError ? <p className="control-error" role="alert">{requestError}</p> : null}
      {operation ? <section className={`panel operation-card ${operation.state.toLowerCase()}`} aria-live="polite"><div><span>현재 작업</span><h3>{operation.stage_ko}</h3><small>시작 {formatKstTime(operation.started_ts_ms)} · 마지막 변경 {formatKstTime(operation.updated_ts_ms)}</small></div><span className="operation-state">{operation.state}</span>{operation.error_message_ko ? <p>{operation.error_message_ko}</p> : null}{operation.state === 'FAILED_RETRYABLE' ? <button type="button" className="primary-button" onClick={onRetry}>다시 시도</button> : null}</section> : null}
      <section className="metric-strip league-home-summary" aria-label="독립 전략 리그 요약">
        <article><span>6개 독립 전략 합계</span><b>{sum(baseAccounts, 'current').toFixed(2)} USDT</b><small>한 개의 실제 1,000 USDT 계좌 결과가 아닙니다.</small></article>
        <article><span>BASE 누적 순손익</span><b>{sum(baseAccounts, 'pnl').toFixed(4)} USDT</b></article>
        <article><span>진행 중 전략 거래</span><b>{baseAccounts.reduce((total, account) => total + account.open_positions, 0)}건</b></article>
        <article><span>완료한 전략 거래</span><b>{baseAccounts.reduce((total, account) => total + account.trade_count, 0)}건</b></article>
        <article><span>현재 1위 전략</span><b>{leadingStrategy ? `${leadingStrategy.short_name} · ${leadingStrategy.display_name_ko}` : '표본 없음'}</b><small>{leadingStrategy ? modeLabels[leadingStrategy.mode] : '데이터를 기다리는 중'}</small></article>
      </section>
      <section className="panel shared-benchmark-card"><div><p className="section-kicker">SHARED CAPITAL BENCHMARK</p><h3>공동계좌 비교 기준</h3><p>전략 경쟁 결과와 분리된 1,000 USDT PAPER 기준계좌입니다.</p></div><dl><div><dt>시작자산</dt><dd>{data.status.starting_equity_usdt.toFixed(2)} USDT</dd></div><div><dt>현재자산</dt><dd>{data.status.current_equity_usdt.toFixed(2)} USDT</dd></div><div><dt>순손익</dt><dd>{sharedPnl.toFixed(4)} USDT</dd></div><div><dt>열린 포지션</dt><dd>{data.position ? 1 : 0}건</dd></div></dl></section>
      <section className="home-shortcuts" aria-label="주요 화면 바로가기"><button type="button" className="primary-button" onClick={() => onNavigate('strategies')}>전략 리그 보기</button><button type="button" className="secondary-button" onClick={() => onNavigate('positions')}>진행 거래 보기</button><button type="button" className="secondary-button" onClick={() => onNavigate('terminal')}>고급 터미널 열기</button></section>
    </section>
  )
}
