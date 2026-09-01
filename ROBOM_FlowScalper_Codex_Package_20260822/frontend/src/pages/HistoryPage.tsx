// 종료된 PAPER 거래는 쉬운 핵심 결과를 먼저 보여주고 원장 식별자는 기술 상세로 분리한다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import { SideDrawer } from '../components/SideDrawer'
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
import { formatKstDateTime, formatKstTime } from '../time'
import { collapseTradeOpportunities } from '../tradeOpportunities'
import type { HistoryRow, StrategySummaryRow, TradesResponse } from '../types'

type Props = {
  rows: HistoryRow[]
  counts?: Pick<
    TradesResponse['counts'],
    'raw_result_rows' | 'base_result_rows' | 'stress_result_rows'
  >
  currentRunId: string
  openPositionCount?: number
  historyScope?: { strategy_version: string; excluded_prior_version_samples: number }
  strategies?: StrategySummaryRow[]
  providedScope?: 'DASHBOARD_MAIN' | 'CURRENT_ALL'
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

function isCurrentStrategyVersion(row: HistoryRow, currentVersion?: string) {
  return !currentVersion || row.strategy_version === currentVersion
}

function milestoneDuration(value: number | null | undefined, empty = '미도달') {
  return value === null || value === undefined ? empty : formatDurationMs(value)
}

function earliestTimestamp(values: Array<number | null | undefined>) {
  const recorded = values.filter((value): value is number => (
    typeof value === 'number' && Number.isFinite(value)
  ))
  return recorded.length ? Math.min(...recorded) : null
}

function milestoneTimestamp(
  row: HistoryRow,
  milestone: 'TP1' | 'TP2' | 'STOP',
) {
  if (milestone === 'TP1') {
    return row.tp1_hit_ts_ms
      ?? (row.time_to_tp1_ms === null || row.time_to_tp1_ms === undefined
        ? null
        : row.entry_ts_ms + row.time_to_tp1_ms)
  }
  if (milestone === 'TP2') {
    return row.tp2_hit_ts_ms
      ?? (row.time_to_tp2_ms === null || row.time_to_tp2_ms === undefined
        ? null
        : row.entry_ts_ms + row.time_to_tp2_ms)
  }
  if (row.exit_reason === 'STOP' || row.exit_reason === 'STOP_LOSS') return row.exit_ts_ms
  return row.time_to_stop_ms === null || row.time_to_stop_ms === undefined
    ? null
    : row.entry_ts_ms + row.time_to_stop_ms
}

function milestoneClockLabel(
  row: HistoryRow,
  milestone: 'TP1' | 'TP2' | 'STOP',
  empty: string,
) {
  const timestamp = milestoneTimestamp(row, milestone)
  if (timestamp === null) return empty
  return `${formatKstDateTime(timestamp)} · 진입 후 ${formatDurationMs(timestamp - row.entry_ts_ms)}`
}

function movementInR(value: string | null | undefined) {
  return value === null || value === undefined || value === '' ? '측정 전' : `${value}R`
}

function optionalUsdt(value: string | null | undefined, signed = false) {
  return value === null || value === undefined || value === ''
    ? '측정 전'
    : formatUsdt(value, { signed })
}

function fillIntentLabel(intent: string) {
  if (intent === 'ENTRY_IOC') return '진입 체결'
  if (intent === 'TAKE_PROFIT') return '목표가 청산'
  if (intent === 'STOP_EXIT') return '손절 청산'
  if (intent === 'EDGE_DECAY_EXIT') return '진입 근거 약화 청산'
  if (intent === 'EMERGENCY_EXIT') return '안전 청산'
  if (intent === 'MANUAL_PAPER_EXIT') return '수동 모의청산'
  return '체결'
}

function fillSideLabel(side: string) {
  if (side === 'BUY') return '매수'
  if (side === 'SELL') return '매도'
  return side
}

function fillUnavailableLabel(row: HistoryRow) {
  if (row.fill_evidence_state === 'CURRENT_MAIN_NO_FILL') return '현재 공동 가상계좌 체결 없음 · NOT_PROVEN'
  if (row.fill_evidence_state === 'SHADOW_UNAVAILABLE') return '전략별 가상계좌 원시 fill 미제공 · NOT_PROVEN'
  return '과거 기록 원시 fill 미제공 · NOT_PROVEN'
}

function mergedFills(rows: HistoryRow[]) {
  const unique = new Map<string, NonNullable<HistoryRow['fills']>[number]>()
  for (const fill of rows.flatMap((row) => row.fills ?? [])) {
    unique.set(`${fill.order_id}:${fill.fill_id}`, fill)
  }
  return [...unique.values()].sort((left, right) => (
    left.ts_ms - right.ts_ms || left.fill_id.localeCompare(right.fill_id)
  ))
}

function mergedFillEvidence(rows: HistoryRow[], fills: NonNullable<HistoryRow['fills']>) {
  if (fills.length > 0 && rows.every((row) => row.fill_evidence_state === 'PRESENT')) {
    return {
      fill_evidence_state: 'PRESENT' as const,
      fill_evidence_reason_ko: rows[0]?.fill_evidence_reason_ko,
    }
  }
  const unavailable = ['LEGACY_UNAVAILABLE', 'SHADOW_UNAVAILABLE', 'CURRENT_MAIN_NO_FILL']
    .flatMap((state) => rows.filter((row) => row.fill_evidence_state === state))[0]
  return {
    fill_evidence_state: unavailable?.fill_evidence_state,
    fill_evidence_reason_ko: unavailable?.fill_evidence_reason_ko,
  }
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

function historyOpportunityKey(row: HistoryRow): string | null {
  const explicitId = [row.opportunity_id, row.candidate_id, row.signal_event_id]
    .find((value) => value && value !== 'UNKNOWN')
  if (!explicitId) return null
  return [
    row.run_id,
    row.strategy,
    row.strategy_version ?? 'UNKNOWN',
    explicitId,
    row.symbol,
    row.side,
  ].join(':')
}

function historyAccountGroupKey(row: HistoryRow) {
  const scope = row.account_scope ?? 'MAIN'
  const accountGroup = scope === 'LEAGUE' ? row.strategy : row.account_id ?? 'SHARED_PAPER'
  return `${scope}:${accountGroup}`
}

function historyResultKey(row: HistoryRow) {
  return `${historyAccountGroupKey(row)}:${row.profile}`
}

function historyResultIdentity(row: HistoryRow) {
  return `${historyResultKey(row)}:${row.trade_id}`
}

function profileOrder(profile: string) {
  if (profile === 'BASE') return 0
  if (profile === 'STRESS') return 1
  return 2
}

function accountOrder(row: HistoryRow) {
  return (row.account_scope ?? 'MAIN') === 'MAIN' ? 0 : 1
}

function sumHistoryRows(
  rows: HistoryRow[],
  key: 'quantity' | 'gross_pnl' | 'fees' | 'slippage' | 'net_pnl',
) {
  return String(rows.reduce((total, row) => total + Number(row[key] || 0), 0))
}

function collapsePartialExitRows(rows: HistoryRow[]) {
  const latest = [...rows].sort((left, right) => right.exit_ts_ms - left.exit_ts_ms)[0]
  const representative = rows.find((row) => row.replay_available) ?? latest
  const fills = mergedFills(rows)
  const fillEvidence = mergedFillEvidence(rows, fills)
  const entryTsMs = Math.min(...rows.map((row) => row.entry_ts_ms))
  const tp1HitTsMs = earliestTimestamp(rows.map((row) => milestoneTimestamp(row, 'TP1')))
  const tp2HitTsMs = earliestTimestamp(rows.map((row) => milestoneTimestamp(row, 'TP2')))
  const stopHitTsMs = earliestTimestamp(rows.map((row) => milestoneTimestamp(row, 'STOP')))
  return {
    ...representative,
    entry_ts_ms: entryTsMs,
    exit_ts_ms: Math.max(...rows.map((row) => row.exit_ts_ms)),
    ...(representative.tp1_hit_ts_ms === null || representative.tp1_hit_ts_ms === undefined
      ? {}
      : { tp1_hit_ts_ms: tp1HitTsMs }),
    ...(representative.tp2_hit_ts_ms === null || representative.tp2_hit_ts_ms === undefined
      ? {}
      : { tp2_hit_ts_ms: tp2HitTsMs }),
    time_to_tp1_ms: tp1HitTsMs === null ? null : Math.max(0, tp1HitTsMs - entryTsMs),
    time_to_tp2_ms: tp2HitTsMs === null ? null : Math.max(0, tp2HitTsMs - entryTsMs),
    time_to_stop_ms: stopHitTsMs === null ? null : Math.max(0, stopHitTsMs - entryTsMs),
    exit: latest.exit,
    quantity: sumHistoryRows(rows, 'quantity'),
    gross_pnl: sumHistoryRows(rows, 'gross_pnl'),
    fees: sumHistoryRows(rows, 'fees'),
    slippage: sumHistoryRows(rows, 'slippage'),
    net_pnl: sumHistoryRows(rows, 'net_pnl'),
    holding_ms: Math.max(...rows.map((row) => row.holding_ms)),
    holding_seconds: Math.max(...rows.map((row) => row.holding_seconds)),
    replay_available: rows.some((row) => row.replay_available),
    fills,
    ...fillEvidence,
  }
}

function groupHistoryOpportunities(rows: HistoryRow[]) {
  const grouped = new Map<string, HistoryRow[]>()
  for (const row of [...rows].sort((left, right) => right.exit_ts_ms - left.exit_ts_ms)) {
    const key = historyOpportunityKey(row)
    if (!key) continue
    grouped.set(key, [...(grouped.get(key) ?? []), row])
  }
  return [...grouped.entries()].map(([key, opportunityRows]): HistoryOpportunity => {
    const resultGroups = new Map<string, HistoryRow[]>()
    for (const row of opportunityRows) {
      const resultKey = historyResultKey(row)
      resultGroups.set(resultKey, [...(resultGroups.get(resultKey) ?? []), row])
    }
    const orderedRows = [...resultGroups.values()].map(collapsePartialExitRows).sort((left, right) => (
      accountOrder(left) - accountOrder(right)
      || profileOrder(left.profile) - profileOrder(right.profile)
      || right.exit_ts_ms - left.exit_ts_ms
    ))
    return { key, rows: orderedRows, primary: orderedRows[0] }
  }).sort((left, right) => (
    Math.max(...right.rows.map((row) => row.exit_ts_ms))
    - Math.max(...left.rows.map((row) => row.exit_ts_ms))
  ))
}

function opportunityComparison(opportunity: HistoryOpportunity) {
  const accountGroupCount = new Set(opportunity.rows.map(historyAccountGroupKey)).size
  const profileCount = new Set(opportunity.rows.map((row) => row.profile)).size
  const comparesAccounts = accountGroupCount > 1
  const comparesCosts = profileCount > 1
  if (!comparesAccounts) {
    return {
      label: comparesCosts ? '비용 2개 비교' : costProfileLabel(opportunity.primary.profile),
      button: comparesCosts ? '비용별 결과' : '자세히',
      note: '같은 진입기회 · 비용 가정만 다름',
      drawerNote: '같은 시점에 들어간 한 번의 진입기회를 비용 조건 2개로 나눠 계산했습니다. 중복 거래가 아닙니다.',
      groupLabel: '비용별 거래 결과',
      comparesAccounts,
    }
  }
  return {
    label: comparesCosts ? '계좌·비용 결과 비교' : '계좌 결과 비교',
    button: '결과 비교',
    note: comparesCosts
      ? '같은 진입기회 · 가상계좌와 비용 조건별 결과'
      : '같은 진입기회 · 가상계좌별 결과',
    drawerNote: comparesCosts
      ? '같은 진입기회를 공동·전략별 가상계좌와 비용 조건별로 나눠 계산했습니다. 중복 진입기회가 아닙니다.'
      : '같은 진입기회를 공동·전략별 가상계좌로 나눠 계산했습니다. 중복 진입기회가 아닙니다.',
    groupLabel: '진입기회별 세부 결과',
    comparesAccounts,
  }
}

function historyResultLabel(opportunity: HistoryOpportunity, row: HistoryRow) {
  return opportunityComparison(opportunity).comparesAccounts
    ? `${accountLabel(row)} · ${costProfileLabel(row.profile)}`
    : costProfileLabel(row.profile)
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
  counts,
  currentRunId,
  openPositionCount = 0,
  historyScope,
  strategies = [],
  providedScope = 'DASHBOARD_MAIN',
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
  const [queryGroupingStatus, setQueryGroupingStatus] = useState<'PROVEN' | 'NOT_PROVEN' | null>(null)
  const [querySourceStatus, setQuerySourceStatus] = useState<TradesResponse['source_status']>(undefined)
  const [queryUnresolvedCount, setQueryUnresolvedCount] = useState(0)
  const [queryCounts, setQueryCounts] = useState<TradesResponse['counts'] | null>(null)
  const [lastQueryUpdateMs, setLastQueryUpdateMs] = useState<number | null>(null)
  const [refreshRevision, setRefreshRevision] = useState(0)
  const [selectedTrade, setSelected] = useState<HistoryRow | null>(null)
  const providedCoversFilters = providedScope === 'CURRENT_ALL'
    ? runFilter === 'CURRENT'
      && versionFilter === 'CURRENT'
      && accountFilter === 'ALL'
      && profileFilter === 'ALL'
      && filter === 'ALL'
    : accountFilter === 'MAIN' && runFilter === 'CURRENT' && versionFilter === 'CURRENT'
  const needsLedgerQuery = !providedCoversFilters
  const beginQuery = () => {
    setQueriedRows(null)
    setQueryLoading(true)
    setQueryRefreshing(false)
    setQueryError('')
    setQueryGroupingStatus(null)
    setQuerySourceStatus(undefined)
    setQueryUnresolvedCount(0)
    setQueryCounts(null)
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
      void fetchJson<TradesResponse>(`/api/trades?${query}`, { signal: controller.signal }, 12_000)
        .then((response) => {
          if (disposed) return
          if (!Array.isArray(response.opportunities)) throw new Error('invalid grouped trades response')
          setQueriedRows(collapseTradeOpportunities(response))
          setQueryGroupingStatus(response.grouping_status ?? 'PROVEN')
          setQuerySourceStatus(response.source_status)
          setQueryUnresolvedCount(response.counts.unresolved_result_rows ?? 0)
          setQueryCounts(response.counts)
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
  const currentStrategyVersion = historyScope?.strategy_version
  const filtered = useMemo(
    () => sourceRows.filter((row) => (
      (runFilter === 'ALL' || row.run_id === currentRunId)
      && (filter === 'ALL' || row.sample_type === filter)
      && (accountFilter === 'ALL' || (row.account_scope ?? 'MAIN') === accountFilter)
      && (profileFilter === 'ALL' || row.profile === profileFilter)
      && (versionFilter === 'ALL' || isCurrentStrategyVersion(row, currentStrategyVersion))
    )),
    [accountFilter, currentRunId, currentStrategyVersion, filter, profileFilter, runFilter, sourceRows, versionFilter],
  )
  const opportunities = useMemo(() => groupHistoryOpportunities(filtered), [filtered])
  const localUnresolvedCount = useMemo(
    () => filtered.filter((row) => historyOpportunityKey(row) === null).length,
    [filtered],
  )
  const unresolvedCount = needsLedgerQuery ? queryUnresolvedCount : localUnresolvedCount
  const selectedOpportunity = selectedTrade
    ? opportunities.find((opportunity) => (
      opportunity.rows.some((row) => (
        historyResultIdentity(row) === historyResultIdentity(selectedTrade)
      ))
    )) ?? null
    : null
  const selected = selectedOpportunity && selectedTrade
    ? selectedOpportunity.rows.find((row) => (
      historyResultIdentity(row) === historyResultIdentity(selectedTrade)
    )) ?? null
    : null
  const mainCount = opportunities.filter((opportunity) => (
    opportunity.rows.some((row) => (row.account_scope ?? 'MAIN') === 'MAIN')
  )).length
  const leagueCount = opportunities.filter((opportunity) => (
    opportunity.rows.some((row) => row.account_scope === 'LEAGUE')
  )).length
  const visibleCounts = needsLedgerQuery
    ? queryCounts
    : counts ?? {
      unique_opportunities: opportunities.length,
      raw_result_rows: filtered.length,
      base_result_rows: filtered.filter((row) => row.profile === 'BASE').length,
      stress_result_rows: filtered.filter((row) => row.profile === 'STRESS').length,
    }
  const visibleQueryLoading = needsLedgerQuery && queryLoading
  const visiblePriorVersionCount = (
    needsLedgerQuery && queriedRows !== null
      ? filtered.filter((row) => row.strategy_version && !isCurrentStrategyVersion(row, currentStrategyVersion)).length
      : historyScope?.excluded_prior_version_samples ?? 0
  )
  const missingMilestone = (row: HistoryRow, currentVersionText: string) => (
    isCurrentStrategyVersion(row, currentStrategyVersion) ? currentVersionText : '과거 기록 없음'
  )
  const isPriorVersion = (row: HistoryRow) => Boolean(row.strategy_version && !isCurrentStrategyVersion(row, currentStrategyVersion))

  return (
    <section aria-labelledby="history-heading">
      <div className="page-heading">
        <div>
          <p className="section-kicker">모의거래 결과</p>
          <h2 id="history-heading">거래 기록</h2>
          <p className="heading-help">종료된 모의거래의 결과와 이유를 쉽게 확인합니다.</p>
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
      {(queryGroupingStatus === 'NOT_PROVEN' || unresolvedCount > 0) ? (
        <p className="league-warning" role="status">
          {querySourceStatus === 'NOT_PROVEN_RAW_LIMIT_BOUNDARY'
            ? '조회 상한에 닿아 가장 오래된 진입기회의 완전성을 확인할 수 없습니다. 표시된 결과를 전체 원장으로 간주하지 마세요.'
            : `정확한 6키 연결 근거가 없는 원장 ${unresolvedCount}행은 진입기회 수에서 제외했습니다.`}
        </p>
      ) : null}
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
        {visibleQueryLoading ? '거래 기록을 불러오는 중입니다.' : `진입기회 ${opportunities.length}건 · 원장 결과 ${visibleCounts?.raw_result_rows ?? 0}행 · BASE ${visibleCounts?.base_result_rows ?? 0} · STRESS ${visibleCounts?.stress_result_rows ?? 0} · 공동 ${mainCount}건 · 전략별 ${leagueCount}건`}
        {visiblePriorVersionCount ? ` · 과거 버전 ${visiblePriorVersionCount}건은 안전하게 보관 중` : ''}
      </p>
      <div className="history-layout">
        <section className="panel wide-panel table-scroll">
          <table className="history-table">
            <thead><tr><th>거래</th><th>전략·계좌</th><th>진입 → 종료</th><th>최종 결과</th><th>종료 이유</th><th>보기</th></tr></thead>
            <tbody>{opportunities.map((opportunity) => {
              const row = opportunity.primary
              const comparison = opportunityComparison(opportunity)
              const sameExitReason = opportunity.rows.every((item) => item.exit_reason === row.exit_reason)
              const entryTsMs = Math.min(...opportunity.rows.map((item) => item.entry_ts_ms))
              const earliestExitTsMs = Math.min(...opportunity.rows.map((item) => item.exit_ts_ms))
              const latestExitTsMs = Math.max(...opportunity.rows.map((item) => item.exit_ts_ms))
              const exitClock = earliestExitTsMs === latestExitTsMs
                ? formatKstTime(latestExitTsMs)
                : `${formatKstTime(earliestExitTsMs)}~${formatKstTime(latestExitTsMs)}`
              return (
              <tr key={opportunity.key}>
                <td data-label="거래"><strong>{row.symbol}</strong><small>{sideLabel(row.side)} · {sampleTypeLabel(row.sample_type)}</small></td>
                <td data-label="전략·계좌"><strong>{strategyLabel(strategies.find((strategy) => strategy.strategy_id === row.strategy), row.strategy)}</strong><small>{opportunity.rows.length > 1 ? comparison.label : `${accountLabel(row)} · ${comparison.label}`}</small></td>
                <td data-label="진입 → 종료" className="history-time-cell"><strong><time dateTime={new Date(entryTsMs).toISOString()} title={formatKstDateTime(entryTsMs)}>{formatKstTime(entryTsMs)}</time> 진입</strong><small><time dateTime={new Date(latestExitTsMs).toISOString()} title={formatKstDateTime(latestExitTsMs)}>{exitClock}</time> 종료 · {opportunityHoldingLabel(opportunity)} 보유</small></td>
                <td data-label="최종 결과">{opportunity.rows.length > 1 ? <><div className="history-cost-results">{opportunity.rows.map((result) => <span className={Number(result.net_pnl) >= 0 ? 'positive' : 'negative'} key={historyResultIdentity(result)}><b>{historyResultLabel(opportunity, result)}</b><strong>{formatUsdt(result.net_pnl, { signed: true })}</strong></span>)}</div><small>{comparison.note}</small></> : <><strong className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</strong><small>가격 손익 {formatUsdt(row.gross_pnl, { signed: true })} · 총비용 {formatUsdt(Number(row.fees) + Number(row.slippage))}</small></>}</td>
                <td data-label="종료 이유"><strong>{sameExitReason ? historyExitLabel(row.exit_reason, isPriorVersion(row)) : '결과별 종료 다름'}</strong><small>{sameExitReason ? exitExplanation(row.exit_reason, isPriorVersion(row)) : '세부 결과를 열어 각각의 종료 이유를 확인하세요.'}</small></td>
                <td data-label="보기"><div className="table-actions"><button type="button" className="table-button" onClick={() => setSelected(row)}>{comparison.button}</button><button type="button" className="table-button" disabled={row.replay_available === false} title={row.replay_available === false ? '저장된 공개시장 데이터가 없습니다.' : undefined} onClick={() => onReplay(row)}>{row.replay_available === false ? '다시보기 없음' : '다시보기'}</button></div></td>
              </tr>
              )
            })}</tbody>
          </table>
          {!visibleQueryLoading && !queryError && opportunities.length === 0 ? <p className="empty-copy">현재 조건에는 끝난 모의거래가 없습니다. 위의 ‘기록 범위 바꾸기’에서 과거 기록도 확인할 수 있습니다.</p> : null}
        </section>
        <SideDrawer
          title={selected ? `${selected.symbol} 거래 결과` : '거래 결과'}
          open={selected !== null}
          onClose={() => setSelected(null)}
          label="거래 상세"
        >
          {selected ? <>
            {selectedOpportunity && selectedOpportunity.rows.length > 1 ? <><p className="history-opportunity-note">{opportunityComparison(selectedOpportunity).drawerNote}</p><div className="history-profile-tabs" role="group" aria-label={opportunityComparison(selectedOpportunity).groupLabel}>{selectedOpportunity.rows.map((row) => <button type="button" aria-pressed={historyResultIdentity(row) === historyResultIdentity(selected)} key={historyResultIdentity(row)} onClick={() => setSelected(row)}><span>{historyResultLabel(selectedOpportunity, row)}</span><strong className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</strong></button>)}</div></> : null}
            <p className="trade-result-lead"><strong className={Number(selected.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(selected.net_pnl, { signed: true })}</strong><span>{historyExitLabel(selected.exit_reason, isPriorVersion(selected))} · {formatDurationMs(selected.holding_ms)} 보유</span></p>
            <section className="trade-detail-section" aria-labelledby="trade-timeline-heading">
              <h3 id="trade-timeline-heading">진입부터 종료까지</h3>
              <dl className="trade-time-flow" aria-label="진입부터 종료까지 시간 흐름">
                <div className="reached"><dt>진입</dt><dd><strong>{formatKstDateTime(selected.entry_ts_ms)}</strong><small>진입가 {formatPrice(selected.entry)}</small></dd></div>
                <div className={milestoneTimestamp(selected, 'TP1') === null ? 'not-reached' : 'reached'}><dt>1차 익절</dt><dd><strong>{milestoneClockLabel(selected, 'TP1', missingMilestone(selected, '미도달'))}</strong><small>{selected.take_profit_1 ? `목표가 ${formatPrice(selected.take_profit_1)}` : '과거 기록에는 1차 목표가가 없습니다.'}</small></dd></div>
                <div className={milestoneTimestamp(selected, 'TP2') === null ? 'not-reached' : 'reached'}><dt>2차 익절</dt><dd><strong>{milestoneClockLabel(selected, 'TP2', missingMilestone(selected, '미도달'))}</strong><small>{selected.take_profit_2 ? `목표가 ${formatPrice(selected.take_profit_2)}` : '과거 기록에는 2차 목표가가 없습니다.'}</small></dd></div>
                <div className={milestoneTimestamp(selected, 'STOP') === null ? 'not-reached' : 'stop-reached'}><dt>손절</dt><dd><strong>{milestoneClockLabel(selected, 'STOP', missingMilestone(selected, '미도달'))}</strong><small>초기 손절가 {formatPrice(selected.initial_stop)}</small></dd></div>
                <div className="closed"><dt>최종 종료</dt><dd><strong>{formatKstDateTime(selected.exit_ts_ms)}</strong><small>{historyExitLabel(selected.exit_reason, isPriorVersion(selected))} · 총 {formatDurationMs(selected.holding_ms)} 보유</small></dd></div>
              </dl>
            </section>
            <section className="trade-detail-section" aria-labelledby="trade-summary-heading">
              <h3 id="trade-summary-heading">거래 요약</h3>
              <dl className="detail-list">
              <div><dt>거래 방향</dt><dd>{sideLabel(selected.side)}</dd></div>
              <div><dt>사용 전략</dt><dd>{strategyLabel(strategies.find((strategy) => strategy.strategy_id === selected.strategy), selected.strategy)}</dd></div>
              <div><dt>진입 가격</dt><dd>{formatPrice(selected.entry)}</dd></div>
              <div><dt>손절 가격</dt><dd>{formatPrice(selected.initial_stop)}</dd></div>
              {selected.take_profit_1 || selected.take_profit_2 ? <><div><dt>1차 목표</dt><dd>{selected.take_profit_1 ? formatPrice(selected.take_profit_1) : '—'}</dd></div><div><dt>2차 목표</dt><dd>{selected.take_profit_2 ? formatPrice(selected.take_profit_2) : '—'}</dd></div></> : <div><dt>목표가(과거 기록)</dt><dd>{formatPrice(selected.take_profit)}</dd></div>}
              <div><dt>종료 가격</dt><dd>{formatPrice(selected.exit)}</dd></div>
              <div><dt>가격 손익</dt><dd>{formatUsdt(selected.gross_pnl, { signed: true })}</dd></div>
              <div><dt>최종 순손익</dt><dd>{formatUsdt(selected.net_pnl, { signed: true })}</dd></div>
              </dl>
            </section>
            <section className="trade-detail-section" aria-labelledby="trade-fills-heading">
              <h3 id="trade-fills-heading">체결 상세</h3>
              {selected.fill_evidence_state === 'PRESENT' && selected.fills?.length ? (
                <ol className="trade-fill-list" aria-label="확인된 PAPER 체결 원장">
                  {selected.fills.map((fill, index) => (
                    <li key={`${fill.order_id}:${fill.fill_id}`}>
                      <p><strong>{index + 1}. {fillIntentLabel(fill.intent)}</strong><span>{fillSideLabel(fill.side)}</span></p>
                      <dl>
                        <div><dt>체결 시각</dt><dd>{formatKstTime(fill.ts_ms)} KST</dd></div>
                        <div><dt>체결 가격</dt><dd>{formatPrice(fill.price)}</dd></div>
                        <div><dt>체결 수량</dt><dd>{formatQuantity(fill.quantity)}</dd></div>
                        <div><dt>수수료</dt><dd>{formatUsdt(fill.fee_usdt)}</dd></div>
                        <div><dt>슬리피지</dt><dd>{formatUsdt(fill.slippage_usdt)}</dd></div>
                      </dl>
                    </li>
                  ))}
                </ol>
              ) : <>
                <p className="trade-detail-missing" role="note">{fillUnavailableLabel(selected)}</p>
                <small>{selected.fill_evidence_reason_ko ?? '원시 체결 배열이 없어 진입·종료 요약값을 개별 fill로 추정하지 않습니다.'}</small>
              </>}
            </section>
            <section className="trade-detail-section" aria-labelledby="trade-cost-heading">
              <h3 id="trade-cost-heading">비용과 종료</h3>
              <dl className="detail-list">
                <div><dt>수수료</dt><dd>{formatUsdt(selected.fees)}</dd></div>
                <div><dt>슬리피지</dt><dd>{formatUsdt(selected.slippage)}</dd></div>
                <div><dt>종료 이유</dt><dd>{historyExitLabel(selected.exit_reason, isPriorVersion(selected))}<small>{exitExplanation(selected.exit_reason, isPriorVersion(selected))}</small></dd></div>
              </dl>
            </section>
            <section className="trade-detail-section" aria-labelledby="trade-movement-heading">
              <h3 id="trade-movement-heading">보유 중 움직임</h3>
              <dl className="detail-list">
                <div><dt>최대 유리 변동(MFE)</dt><dd>{movementInR(selected.mfe_r)}</dd></div>
                <div><dt>최대 불리 변동(MAE)</dt><dd>{movementInR(selected.mae_r)}</dd></div>
                <div><dt>고점 대비 되돌림(giveback)</dt><dd>{optionalUsdt(selected.giveback_usdt)}</dd></div>
              </dl>
            </section>
            <details className="advanced-details"><summary>목표 도달시간 자세히</summary><dl className="detail-list"><div><dt>1차 목표까지</dt><dd>{milestoneDuration(selected.time_to_tp1_ms, missingMilestone(selected, '미도달'))}</dd></div><div><dt>2차 목표까지</dt><dd>{milestoneDuration(selected.time_to_tp2_ms, missingMilestone(selected, '미도달'))}</dd></div><div><dt>손절까지</dt><dd>{milestoneDuration(selected.time_to_stop_ms, missingMilestone(selected, selected.exit_reason === 'STOP' ? '기록 확인 필요' : '해당 없음'))}</dd></div></dl></details>
            {selected.trailing_activation_ts_ms !== null && selected.trailing_activation_ts_ms !== undefined ? <details className="advanced-details"><summary>추적 익절 자세히</summary><dl className="detail-list"><div><dt>활성화</dt><dd>{formatDurationMs(Math.max(0, selected.trailing_activation_ts_ms - selected.entry_ts_ms))} 뒤</dd></div><div><dt>남은 수량 추적</dt><dd>{selected.runner_started_ts_ms !== null && selected.runner_started_ts_ms !== undefined ? `${formatDurationMs(Math.max(0, selected.runner_started_ts_ms - selected.entry_ts_ms))} 뒤` : '시작 안 됨'}</dd></div><div><dt>최고 미실현 손익</dt><dd>{formatUsdt(selected.peak_unrealized_usdt ?? '0', { signed: true })}</dd></div><div><dt>고점 대비 되돌림</dt><dd>{formatUsdt(selected.giveback_usdt ?? '0')}</dd></div><div><dt>남은 수량 순기여</dt><dd>{formatUsdt(selected.runner_net_pnl_usdt ?? '0', { signed: true })}</dd></div></dl></details> : null}
            <details className="advanced-details"><summary>기술 정보</summary><h3 className="trade-technical-heading">기술 상세</h3><dl className="detail-list"><div><dt>진입기회 ID</dt><dd>{selected.opportunity_id ?? '없음'}</dd></div><div><dt>후보 ID</dt><dd>{selected.candidate_id ?? '없음'}</dd></div><div><dt>이벤트 ID</dt><dd>{selected.signal_event_id ?? '없음'}</dd></div><div><dt>상태 checksum</dt><dd>{selected.trailing_state_checksum ?? '없음'}</dd></div><div><dt>원본 설정 hash</dt><dd>{selected.config_hash ?? '없음'}</dd></div><div><dt>거래 ID</dt><dd>{selected.trade_id}</dd></div><div><dt>실행 ID</dt><dd>{selected.run_id}</dd></div><div><dt>전략 코드</dt><dd>{selected.strategy}</dd></div><div><dt>전략 버전</dt><dd>{selected.strategy_version ?? '과거 기록'}</dd></div><div><dt>계좌 코드</dt><dd>{selected.account_id ?? accountLabel(selected)}</dd></div><div><dt>종료 코드</dt><dd>{selected.exit_reason}</dd></div><div><dt>수량</dt><dd>{formatQuantity(selected.quantity)}</dd></div></dl></details>
            <section className="trade-detail-section trade-replay-action" aria-labelledby="trade-replay-heading">
              <h3 id="trade-replay-heading">다시보기</h3>
              <p>{selected.replay_available === false ? '저장된 공개시장 이벤트가 없어 다시보기를 실행할 수 없습니다.' : '선택한 비용 조건의 저장 이벤트를 순서대로 확인합니다.'}</p>
              <button type="button" className="primary-button full-width" disabled={selected.replay_available === false} onClick={() => onReplay(selected)}>선택한 비용 결과 다시보기</button>
            </section>
          </> : null}
        </SideDrawer>
      </div>
    </section>
  )
}
