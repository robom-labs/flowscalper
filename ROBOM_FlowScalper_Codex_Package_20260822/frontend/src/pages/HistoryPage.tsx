// 종료된 PAPER 거래는 쉬운 핵심 결과를 먼저 보여주고 원장 식별자는 기술 정보로 분리한다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import {
  costProfileLabel,
  exitReasonLabel,
  formatDurationMs,
  formatPrice,
  formatQuantity,
  formatUsdt,
  sampleTypeLabel,
  sideLabel,
} from '../format'
import { strategyLabel } from '../strategyPresentation'
import type { HistoryResponse, HistoryRow } from '../types'

type Props = {
  rows: HistoryRow[]
  currentRunId: string
  historyScope?: { strategy_version: string; excluded_prior_version_samples: number }
  onReplay: (trade: HistoryRow) => void
}
type Filter = 'ALL' | 'LIVE_PUBLIC' | 'OFFLINE_FIXTURE'
type RunFilter = 'CURRENT' | 'ALL'
type AccountFilter = 'MAIN' | 'LEAGUE' | 'ALL'
type ProfileFilter = 'ALL' | 'BASE' | 'STRESS'
type VersionFilter = 'CURRENT' | 'ALL'

function milestoneDuration(value: number | null | undefined, empty = '미도달') {
  return value === null || value === undefined ? empty : formatDurationMs(value)
}

function accountLabel(row: HistoryRow) {
  return row.account_scope === 'LEAGUE' ? '전략별 가상계좌' : '공동 가상계좌'
}

function isEdgeDecay(reason: string) {
  return reason === 'EDGE_DECAY' || reason === 'EXIT_EDGE_DECAY'
}

function historyExitLabel(reason: string, priorVersion = false) {
  if (priorVersion && isEdgeDecay(reason)) return '진입 근거 약화(과거 기준)'
  return exitReasonLabel(reason)
}

function exitExplanation(reason: string, priorVersion = false) {
  if (priorVersion && isEdgeDecay(reason)) return '과거 기준에서 진입 근거 약화로 종료된 기록입니다. 현재 버전은 비용 이상의 가격 악화도 함께 확인합니다.'
  if (isEdgeDecay(reason)) return '가격이 왕복 비용 구간보다 불리하게 움직이고 진입 근거도 함께 약해져 종료했습니다.'
  if (reason.includes('TAKE_PROFIT') || reason === 'TP1' || reason === 'TP2') return '미리 정한 목표 가격에 도달해 이익을 확정했습니다.'
  if (reason === 'STOP' || reason === 'STOP_LOSS') return '미리 정한 손절 가격에 도달해 손실을 제한했습니다.'
  if (reason.includes('PROFIT_PROTECTION')) return '이익 구간에 진입한 뒤 흐름이 약해져 남은 이익을 보호했습니다.'
  if (reason.includes('STALE')) return '시장데이터 안전 기준을 지키기 위해 종료했습니다.'
  if (reason.includes('MAX_HOLD')) return '전략이 정한 최대 보유시간에 도달해 종료했습니다.'
  return '저장된 PAPER 종료 규칙에 따라 종료했습니다.'
}

export function HistoryPage({ rows, currentRunId, historyScope, onReplay }: Props) {
  const [filter, setFilter] = useState<Filter>('ALL')
  const [runFilter, setRunFilter] = useState<RunFilter>('CURRENT')
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('ALL')
  const [profileFilter, setProfileFilter] = useState<ProfileFilter>('ALL')
  const [versionFilter, setVersionFilter] = useState<VersionFilter>('CURRENT')
  const [queriedRows, setQueriedRows] = useState<HistoryRow[] | null>(null)
  const [queryLoading, setQueryLoading] = useState(true)
  const [queryError, setQueryError] = useState('')
  const [selectedTrade, setSelected] = useState<HistoryRow | null>(null)
  const needsLedgerQuery = accountFilter !== 'MAIN' || versionFilter !== 'CURRENT'
  const beginQuery = () => {
    setQueriedRows(null)
    setQueryLoading(true)
    setQueryError('')
  }

  useEffect(() => {
    if (!needsLedgerQuery) return
    const controller = new AbortController()
    const query = new URLSearchParams({
      run_scope: runFilter,
      account_scope: accountFilter,
      profile: profileFilter,
      version_scope: versionFilter,
      sample_type: filter,
      limit: '1000',
    })
    const load = () => {
      void fetchJson<HistoryResponse>(`/api/history?${query}`, { signal: controller.signal }, 12_000)
        .then((response) => { setQueriedRows(response.rows); setQueryError('') })
        .catch(() => { if (!controller.signal.aborted) setQueryError('거래 기록을 불러오지 못했습니다. 연결을 확인하세요.') })
        .finally(() => { if (!controller.signal.aborted) setQueryLoading(false) })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [accountFilter, filter, needsLedgerQuery, profileFilter, runFilter, versionFilter])

  const sourceRows = useMemo(
    () => needsLedgerQuery ? queriedRows ?? [] : rows,
    [needsLedgerQuery, queriedRows, rows],
  )
  const isCurrentVersion = (row: HistoryRow) => (
    !historyScope?.strategy_version || row.strategy_version === historyScope.strategy_version
  )
  const filtered = useMemo(
    () => sourceRows.filter((row) => (
      (runFilter === 'ALL' || row.run_id === currentRunId)
      && (filter === 'ALL' || row.sample_type === filter)
      && (profileFilter === 'ALL' || row.profile === profileFilter)
    )),
    [currentRunId, filter, profileFilter, runFilter, sourceRows],
  )
  const selected = selectedTrade && filtered.some((row) => row.trade_id === selectedTrade.trade_id)
    ? selectedTrade
    : null
  const mainCount = filtered.filter((row) => row.account_scope !== 'LEAGUE').length
  const leagueCount = filtered.length - mainCount
  const visibleQueryLoading = needsLedgerQuery && queryLoading
  const visiblePriorVersionCount = (
    needsLedgerQuery && queriedRows !== null
      ? filtered.filter((row) => row.strategy_version && !isCurrentVersion(row)).length
      : historyScope?.excluded_prior_version_samples ?? 0
  )
  const missingMilestone = (row: HistoryRow, currentVersionText: string) => (
    isCurrentVersion(row) ? currentVersionText : '과거 기록 없음'
  )
  const isPriorVersion = (row: HistoryRow) => Boolean(row.strategy_version && !isCurrentVersion(row))

  return (
    <section aria-labelledby="history-heading">
      <div className="page-heading">
        <div>
          <p className="section-kicker">모의거래 결과</p>
          <h2 id="history-heading">거래 기록</h2>
          <p className="heading-help">종료된 모의거래의 결과와 이유를 쉽게 확인합니다. 실제 주문은 없습니다.</p>
        </div>
        <span className="page-note">현재 전략 버전 · 이번 실행 우선</span>
      </div>
      <details className="history-filter-details">
        <summary>기록 범위 바꾸기</summary>
        <div className="history-heading-filters">
          <label className="inline-filter">실행 범위<select aria-label="Run 범위" value={runFilter} onChange={(event) => { beginQuery(); setRunFilter(event.target.value as RunFilter) }}><option value="CURRENT">이번 실행</option><option value="ALL">모든 실행</option></select></label>
          <label className="inline-filter">계좌<select aria-label="계좌 범위" value={accountFilter} onChange={(event) => { beginQuery(); setAccountFilter(event.target.value as AccountFilter) }}><option value="ALL">모든 가상계좌</option><option value="MAIN">공동 가상계좌</option><option value="LEAGUE">전략별 가상계좌</option></select></label>
          <label className="inline-filter">비용 가정<select aria-label="비용 조건" value={profileFilter} onChange={(event) => { beginQuery(); setProfileFilter(event.target.value as ProfileFilter) }}><option value="ALL">기본 + 보수 비용</option><option value="BASE">기본 비용</option><option value="STRESS">보수 비용</option></select></label>
          <label className="inline-filter">전략 버전<select aria-label="전략 버전" value={versionFilter} onChange={(event) => { beginQuery(); setVersionFilter(event.target.value as VersionFilter) }}><option value="CURRENT">현재 버전</option><option value="ALL">과거 버전 포함</option></select></label>
          <label className="inline-filter">데이터<select aria-label="기록 구분" value={filter} onChange={(event) => { beginQuery(); setFilter(event.target.value as Filter) }}><option value="ALL">전체</option><option value="LIVE_PUBLIC">공개시장</option><option value="OFFLINE_FIXTURE">연습용 샘플</option></select></label>
        </div>
      </details>
      {queryError ? <p className="error-banner" role="alert">{queryError}</p> : null}
      <p className="history-result-summary" role="status">
        {visibleQueryLoading ? '거래 기록을 불러오는 중입니다.' : `현재 조건 ${filtered.length}건 · 공동 ${mainCount}건 · 전략별 ${leagueCount}건`}
        {visiblePriorVersionCount ? ` · 과거 버전 ${visiblePriorVersionCount}건은 안전하게 보관 중` : ''}
      </p>
      <div className={selected ? 'history-layout drawer-open' : 'history-layout'}>
        <section className="panel wide-panel table-scroll">
          <table className="history-table">
            <thead><tr><th>거래</th><th>전략·계좌</th><th>최종 결과</th><th>종료</th><th>보유</th><th>보기</th></tr></thead>
            <tbody>{filtered.map((row) => (
              <tr key={`${row.account_scope ?? 'MAIN'}:${row.trade_id}`}>
                <td data-label="거래"><strong>{row.symbol}</strong><small>{sideLabel(row.side)} · {sampleTypeLabel(row.sample_type)}</small></td>
                <td data-label="전략·계좌"><strong>{strategyLabel(undefined, row.strategy)}</strong><small>{accountLabel(row)} · {costProfileLabel(row.profile)}</small></td>
                <td data-label="최종 결과" className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}><strong>{formatUsdt(row.net_pnl, { signed: true })}</strong><small>가격 손익 {formatUsdt(row.gross_pnl, { signed: true })} · 총비용 {formatUsdt(Number(row.fees) + Number(row.slippage))}</small></td>
                <td data-label="종료"><strong>{historyExitLabel(row.exit_reason, isPriorVersion(row))}</strong><small>{exitExplanation(row.exit_reason, isPriorVersion(row))}</small></td>
                <td data-label="보유"><strong>{formatDurationMs(row.holding_ms)}</strong><small>1차 목표 {milestoneDuration(row.time_to_tp1_ms, missingMilestone(row, '미도달'))}</small></td>
                <td data-label="보기"><div className="table-actions"><button type="button" className="table-button" onClick={() => setSelected(row)}>자세히</button><button type="button" className="table-button" disabled={row.replay_available === false} title={row.replay_available === false ? '저장된 공개시장 데이터가 없습니다.' : undefined} onClick={() => onReplay(row)}>{row.replay_available === false ? '다시보기 없음' : '다시보기'}</button></div></td>
              </tr>
            ))}</tbody>
          </table>
          {!visibleQueryLoading && !queryError && filtered.length === 0 ? <p className="empty-copy">현재 조건에는 끝난 모의거래가 없습니다. 위의 ‘기록 범위 바꾸기’에서 과거 기록도 확인할 수 있습니다.</p> : null}
        </section>
        {selected ? (
          <aside className="panel trade-drawer" aria-labelledby="trade-detail-title">
            <div className="panel-title"><h3 id="trade-detail-title">{selected.symbol} 거래 결과</h3><button type="button" className="close-button" aria-label="거래 상세 닫기" onClick={() => setSelected(null)}>닫기</button></div>
            <p className="trade-result-lead"><strong className={Number(selected.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(selected.net_pnl, { signed: true })}</strong><span>{historyExitLabel(selected.exit_reason, isPriorVersion(selected))} · {formatDurationMs(selected.holding_ms)} 보유</span></p>
            <dl className="detail-list">
              <div><dt>거래 방향</dt><dd>{sideLabel(selected.side)}</dd></div>
              <div><dt>사용 전략</dt><dd>{strategyLabel(undefined, selected.strategy)}</dd></div>
              <div><dt>진입 가격</dt><dd>{formatPrice(selected.entry)}</dd></div>
              <div><dt>손절 가격</dt><dd>{formatPrice(selected.initial_stop)}</dd></div>
              {selected.take_profit_1 || selected.take_profit_2 ? <><div><dt>1차 목표</dt><dd>{selected.take_profit_1 ? formatPrice(selected.take_profit_1) : '—'}</dd></div><div><dt>2차 목표</dt><dd>{selected.take_profit_2 ? formatPrice(selected.take_profit_2) : '—'}</dd></div></> : <div><dt>목표가(과거 기록)</dt><dd>{formatPrice(selected.take_profit)}</dd></div>}
              <div><dt>종료 가격</dt><dd>{formatPrice(selected.exit)}</dd></div>
              <div><dt>종료 이유</dt><dd>{historyExitLabel(selected.exit_reason, isPriorVersion(selected))}<small>{exitExplanation(selected.exit_reason, isPriorVersion(selected))}</small></dd></div>
              <div><dt>가격 손익</dt><dd>{formatUsdt(selected.gross_pnl, { signed: true })}</dd></div>
              <div><dt>수수료·가격차이 비용</dt><dd>{formatUsdt(Number(selected.fees) + Number(selected.slippage))}</dd></div>
              <div><dt>최종 순손익</dt><dd>{formatUsdt(selected.net_pnl, { signed: true })}</dd></div>
            </dl>
            <details className="advanced-details"><summary>목표 도달시간 자세히</summary><dl className="detail-list"><div><dt>1차 목표까지</dt><dd>{milestoneDuration(selected.time_to_tp1_ms, missingMilestone(selected, '미도달'))}</dd></div><div><dt>2차 목표까지</dt><dd>{milestoneDuration(selected.time_to_tp2_ms, missingMilestone(selected, '미도달'))}</dd></div><div><dt>손절까지</dt><dd>{milestoneDuration(selected.time_to_stop_ms, missingMilestone(selected, selected.exit_reason === 'STOP' ? '기록 확인 필요' : '해당 없음'))}</dd></div></dl></details>
            {selected.trailing_activation_ts_ms !== null && selected.trailing_activation_ts_ms !== undefined ? <details className="advanced-details"><summary>추적 익절 자세히</summary><dl className="detail-list"><div><dt>활성화</dt><dd>{formatDurationMs(Math.max(0, selected.trailing_activation_ts_ms - selected.entry_ts_ms))} 뒤</dd></div><div><dt>남은 수량 추적</dt><dd>{selected.runner_started_ts_ms !== null && selected.runner_started_ts_ms !== undefined ? `${formatDurationMs(Math.max(0, selected.runner_started_ts_ms - selected.entry_ts_ms))} 뒤` : '시작 안 됨'}</dd></div><div><dt>최고 미실현 손익</dt><dd>{formatUsdt(selected.peak_unrealized_usdt ?? '0', { signed: true })}</dd></div><div><dt>고점 대비 되돌림</dt><dd>{formatUsdt(selected.giveback_usdt ?? '0')}</dd></div><div><dt>남은 수량 순기여</dt><dd>{formatUsdt(selected.runner_net_pnl_usdt ?? '0', { signed: true })}</dd></div></dl></details> : null}
            <details className="advanced-details"><summary>기술 정보</summary><dl className="detail-list"><div><dt>거래 ID</dt><dd>{selected.trade_id}</dd></div><div><dt>실행 ID</dt><dd>{selected.run_id}</dd></div><div><dt>전략 코드</dt><dd>{selected.strategy}</dd></div><div><dt>전략 버전</dt><dd>{selected.strategy_version ?? '과거 기록'}</dd></div><div><dt>계좌 코드</dt><dd>{selected.account_id ?? accountLabel(selected)}</dd></div><div><dt>종료 코드</dt><dd>{selected.exit_reason}</dd></div><div><dt>수량</dt><dd>{formatQuantity(selected.quantity)}</dd></div></dl></details>
            <button type="button" className="primary-button full-width" disabled={selected.replay_available === false} onClick={() => onReplay(selected)}>이 거래 다시보기</button>
          </aside>
        ) : null}
      </div>
    </section>
  )
}
