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
import { formatKstTime } from '../time'
import type { HistoryResponse, HistoryRow } from '../types'

type Props = {
  rows: HistoryRow[]
  currentRunId: string
  openPositionCount?: number
  historyScope?: { strategy_version: string; excluded_prior_version_samples: number }
  onReplay: (trade: HistoryRow) => void
}
type Filter = 'ALL' | 'LIVE_PUBLIC' | 'OFFLINE_FIXTURE'
type RunFilter = 'CURRENT' | 'ALL'
type AccountFilter = 'MAIN' | 'LEAGUE' | 'ALL'
type ProfileFilter = 'ALL' | 'BASE' | 'STRESS'
type VersionFilter = 'CURRENT' | 'ALL'
type HistoryOpportunity = {
  key: string
  rows: HistoryRow[]
  primary: HistoryRow
}

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

function historyOpportunityKey(row: HistoryRow) {
  const scope = row.account_scope ?? 'MAIN'
  if (scope !== 'LEAGUE') return `${scope}:${row.run_id}:${row.trade_id}`
  const explicitId = [row.opportunity_id, row.candidate_id, row.signal_event_id]
    .find((value) => value && value !== 'UNKNOWN')
  const fallbackId = [row.strategy, row.symbol, row.side, row.entry_ts_ms].join(':')
  return `${scope}:${row.run_id}:${row.strategy_version ?? 'UNKNOWN'}:${row.strategy}:${explicitId ?? fallbackId}`
}

function profileOrder(profile: string) {
  if (profile === 'BASE') return 0
  if (profile === 'STRESS') return 1
  return 2
}

function groupHistoryOpportunities(rows: HistoryRow[]) {
  const grouped = new Map<string, HistoryRow[]>()
  for (const row of [...rows].sort((left, right) => right.exit_ts_ms - left.exit_ts_ms)) {
    const baseKey = historyOpportunityKey(row)
    const existing = grouped.get(baseKey)
    const key = existing?.some((item) => item.profile === row.profile)
      ? `${baseKey}:TRADE:${row.trade_id}`
      : baseKey
    grouped.set(key, [...(grouped.get(key) ?? []), row])
  }
  return [...grouped.entries()].map(([key, opportunityRows]): HistoryOpportunity => {
    const orderedRows = [...opportunityRows].sort((left, right) => (
      profileOrder(left.profile) - profileOrder(right.profile)
      || right.exit_ts_ms - left.exit_ts_ms
    ))
    return { key, rows: orderedRows, primary: orderedRows[0] }
  }).sort((left, right) => (
    Math.max(...right.rows.map((row) => row.exit_ts_ms))
    - Math.max(...left.rows.map((row) => row.exit_ts_ms))
  ))
}

function opportunityHoldingLabel(opportunity: HistoryOpportunity) {
  const durations = opportunity.rows.map((row) => row.holding_ms)
  const shortest = Math.min(...durations)
  const longest = Math.max(...durations)
  const shortestLabel = formatDurationMs(shortest)
  const longestLabel = formatDurationMs(longest)
  return shortestLabel === longestLabel ? shortestLabel : `${shortestLabel}~${longestLabel}`
}

export function HistoryPage({
  rows,
  currentRunId,
  openPositionCount = 0,
  historyScope,
  onReplay,
}: Props) {
  const [filter, setFilter] = useState<Filter>('ALL')
  const [runFilter, setRunFilter] = useState<RunFilter>('CURRENT')
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('ALL')
  const [profileFilter, setProfileFilter] = useState<ProfileFilter>('ALL')
  const [versionFilter, setVersionFilter] = useState<VersionFilter>('CURRENT')
  const [queriedRows, setQueriedRows] = useState<HistoryRow[] | null>(null)
  const [queryLoading, setQueryLoading] = useState(true)
  const [queryRefreshing, setQueryRefreshing] = useState(false)
  const [queryError, setQueryError] = useState('')
  const [lastQueryUpdateMs, setLastQueryUpdateMs] = useState<number | null>(null)
  const [refreshRevision, setRefreshRevision] = useState(0)
  const [selectedTrade, setSelected] = useState<HistoryRow | null>(null)
  const needsLedgerQuery = accountFilter !== 'MAIN' || versionFilter !== 'CURRENT'
  const beginQuery = () => {
    setQueriedRows(null)
    setQueryLoading(true)
    setQueryRefreshing(false)
    setQueryError('')
    setLastQueryUpdateMs(null)
  }

  useEffect(() => {
    if (!needsLedgerQuery) return
    let disposed = false
    let inFlight = false
    let controller: AbortController | null = null
    const query = new URLSearchParams({
      run_scope: runFilter,
      account_scope: accountFilter,
      profile: profileFilter,
      version_scope: versionFilter,
      sample_type: filter,
      limit: '1000',
    })
    const load = () => {
      if (inFlight) return
      inFlight = true
      controller = new AbortController()
      setQueryRefreshing(true)
      void fetchJson<HistoryResponse>(`/api/history?${query}`, { signal: controller.signal }, 12_000)
        .then((response) => {
          if (disposed) return
          setQueriedRows(response.rows)
          setQueryError('')
          setLastQueryUpdateMs(Date.now())
        })
        .catch(() => {
          if (!disposed && !controller?.signal.aborted) {
            setQueryError('거래 기록을 불러오지 못했습니다. 연결을 확인하세요.')
          }
        })
        .finally(() => {
          inFlight = false
          if (!disposed) {
            setQueryLoading(false)
            setQueryRefreshing(false)
          }
        })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    return () => {
      disposed = true
      controller?.abort()
      window.clearInterval(timer)
    }
  }, [accountFilter, filter, needsLedgerQuery, profileFilter, refreshRevision, runFilter, versionFilter])

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
  const opportunities = useMemo(() => groupHistoryOpportunities(filtered), [filtered])
  const selectedOpportunity = selectedTrade
    ? opportunities.find((opportunity) => (
      opportunity.rows.some((row) => row.trade_id === selectedTrade.trade_id)
    )) ?? null
    : null
  const selected = selectedOpportunity && selectedTrade
    ? selectedOpportunity.rows.find((row) => row.trade_id === selectedTrade.trade_id) ?? null
    : null
  const mainCount = opportunities.filter((opportunity) => opportunity.primary.account_scope !== 'LEAGUE').length
  const leagueCount = opportunities.length - mainCount
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
      <div className={openPositionCount > 0 ? 'history-live-status active' : 'history-live-status'} aria-live="polite">
        <div className="history-live-copy">
          <strong>현재 진행 중인 모의 포지션 {openPositionCount}건</strong>
          <small>{openPositionCount > 0
            ? '아래에는 종료된 기록만 표시됩니다. 종료되면 자동으로 완료 기록에 추가됩니다.'
            : '현재는 새 진입을 기다리는 중입니다. 아래에서 이미 끝난 기록을 확인할 수 있습니다.'}</small>
        </div>
        {needsLedgerQuery ? (
          <div className="history-refresh-control">
            <small>{queryRefreshing
              ? '거래 기록 확인 중'
              : lastQueryUpdateMs
                ? `5초마다 자동 확인 · 마지막 확인 ${formatKstTime(lastQueryUpdateMs)} KST`
                : '거래 기록 확인 대기'}</small>
            <button
              type="button"
              className="table-button"
              disabled={queryRefreshing}
              onClick={() => setRefreshRevision((revision) => revision + 1)}
            >지금 새로고침</button>
          </div>
        ) : <small className="history-stream-note">공동 가상계좌 기록은 실시간 화면 상태로 자동 반영됩니다.</small>}
      </div>
      <p className="history-result-summary" role="status">
        {visibleQueryLoading ? '거래 기록을 불러오는 중입니다.' : `진입기회 ${opportunities.length}건 · 세부 원장 ${filtered.length}건 · 공동 ${mainCount}건 · 전략별 ${leagueCount}건`}
        {visiblePriorVersionCount ? ` · 과거 버전 ${visiblePriorVersionCount}건은 안전하게 보관 중` : ''}
      </p>
      <div className={selected ? 'history-layout drawer-open' : 'history-layout'}>
        <section className="panel wide-panel table-scroll">
          <table className="history-table">
            <thead><tr><th>거래</th><th>전략·계좌</th><th>최종 결과</th><th>종료</th><th>보유</th><th>보기</th></tr></thead>
            <tbody>{opportunities.map((opportunity) => {
              const row = opportunity.primary
              const sameExitReason = opportunity.rows.every((item) => item.exit_reason === row.exit_reason)
              return (
              <tr key={opportunity.key}>
                <td data-label="거래"><strong>{row.symbol}</strong><small>{sideLabel(row.side)} · {sampleTypeLabel(row.sample_type)}</small></td>
                <td data-label="전략·계좌"><strong>{strategyLabel(undefined, row.strategy)}</strong><small>{accountLabel(row)} · {opportunity.rows.length > 1 ? '비용 2개 비교' : costProfileLabel(row.profile)}</small></td>
                <td data-label="최종 결과">{opportunity.rows.length > 1 ? <><div className="history-cost-results">{opportunity.rows.map((result) => <span className={Number(result.net_pnl) >= 0 ? 'positive' : 'negative'} key={result.trade_id}><b>{costProfileLabel(result.profile)}</b><strong>{formatUsdt(result.net_pnl, { signed: true })}</strong></span>)}</div><small>같은 진입기회 · 비용 가정만 다름</small></> : <><strong className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</strong><small>가격 손익 {formatUsdt(row.gross_pnl, { signed: true })} · 총비용 {formatUsdt(Number(row.fees) + Number(row.slippage))}</small></>}</td>
                <td data-label="종료"><strong>{sameExitReason ? historyExitLabel(row.exit_reason, isPriorVersion(row)) : '비용별 종료 다름'}</strong><small>{sameExitReason ? exitExplanation(row.exit_reason, isPriorVersion(row)) : '비용별 결과를 열어 각각의 종료 이유를 확인하세요.'}</small></td>
                <td data-label="보유"><strong>{opportunityHoldingLabel(opportunity)}</strong><small>1차 목표 {milestoneDuration(row.time_to_tp1_ms, missingMilestone(row, '미도달'))}</small></td>
                <td data-label="보기"><div className="table-actions"><button type="button" className="table-button" onClick={() => setSelected(row)}>{opportunity.rows.length > 1 ? '비용별 결과' : '자세히'}</button><button type="button" className="table-button" disabled={row.replay_available === false} title={row.replay_available === false ? '저장된 공개시장 데이터가 없습니다.' : undefined} onClick={() => onReplay(row)}>{row.replay_available === false ? '다시보기 없음' : '다시보기'}</button></div></td>
              </tr>
              )
            })}</tbody>
          </table>
          {!visibleQueryLoading && !queryError && opportunities.length === 0 ? <p className="empty-copy">현재 조건에는 끝난 모의거래가 없습니다. 위의 ‘기록 범위 바꾸기’에서 과거 기록도 확인할 수 있습니다.</p> : null}
        </section>
        {selected ? (
          <aside className="panel trade-drawer" aria-labelledby="trade-detail-title">
            <div className="panel-title"><h3 id="trade-detail-title">{selected.symbol} 거래 결과</h3><button type="button" className="close-button" aria-label="거래 상세 닫기" onClick={() => setSelected(null)}>닫기</button></div>
            {selectedOpportunity && selectedOpportunity.rows.length > 1 ? <><p className="history-opportunity-note">같은 시점에 들어간 한 번의 진입기회를 비용 조건 2개로 나눠 계산했습니다. 중복 거래가 아닙니다.</p><div className="history-profile-tabs" role="group" aria-label="비용별 거래 결과">{selectedOpportunity.rows.map((row) => <button type="button" aria-pressed={row.trade_id === selected.trade_id} key={row.trade_id} onClick={() => setSelected(row)}><span>{costProfileLabel(row.profile)}</span><strong className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</strong></button>)}</div></> : null}
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
            <details className="advanced-details"><summary>기술 정보</summary><dl className="detail-list"><div><dt>진입기회 ID</dt><dd>{selected.opportunity_id ?? selected.candidate_id ?? selected.signal_event_id ?? '과거 기록'}</dd></div><div><dt>거래 ID</dt><dd>{selected.trade_id}</dd></div><div><dt>실행 ID</dt><dd>{selected.run_id}</dd></div><div><dt>전략 코드</dt><dd>{selected.strategy}</dd></div><div><dt>전략 버전</dt><dd>{selected.strategy_version ?? '과거 기록'}</dd></div><div><dt>계좌 코드</dt><dd>{selected.account_id ?? accountLabel(selected)}</dd></div><div><dt>종료 코드</dt><dd>{selected.exit_reason}</dd></div><div><dt>수량</dt><dd>{formatQuantity(selected.quantity)}</dd></div></dl></details>
            <button type="button" className="primary-button full-width" disabled={selected.replay_available === false} onClick={() => onReplay(selected)}>선택한 비용 결과 다시보기</button>
          </aside>
        ) : null}
      </div>
    </section>
  )
}
