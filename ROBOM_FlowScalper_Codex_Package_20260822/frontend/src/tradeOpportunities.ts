// Backend가 정확히 묶은 6키 진입기회를 계좌·비용별 표시 행으로만 변환한다.
import type { HistoryRow, TradeAccountGroup, TradeOpportunity, TradesResponse } from './types'

const costProfiles = ['BASE', 'STRESS'] as const

function sumRows(rows: HistoryRow[], key: 'quantity' | 'gross_pnl' | 'fees' | 'slippage' | 'net_pnl') {
  return String(rows.reduce((total, row) => total + Number(row[key] || 0), 0))
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

function fallbackAccountGroups(opportunity: TradeOpportunity) {
  const grouped = new Map<string, TradeAccountGroup>()
  for (const row of opportunity.rows) {
    const accountScope = row.account_scope ?? 'MAIN'
    const accountGroupId = accountScope === 'LEAGUE'
      ? opportunity.key.strategy_id
      : row.account_id ?? 'SHARED_PAPER'
    const key = `${accountScope}:${accountGroupId}`
    const current = grouped.get(key) ?? {
      account_scope: accountScope,
      account_group_id: accountGroupId,
      account_ids: [],
      profiles: {},
      profile_account_refs: {},
      rows: [],
      raw_result_row_count: 0,
      base_result_row_count: 0,
      stress_result_row_count: 0,
      partial_exit_row_count: 0,
    }
    const accountId = row.account_id ?? (accountScope === 'MAIN' ? 'SHARED_PAPER' : `${opportunity.key.strategy_id}:${row.profile}`)
    if (!current.account_ids.includes(accountId)) current.account_ids.push(accountId)
    const profile = row.profile === 'STRESS' ? 'STRESS' : 'BASE'
    const existing = current.profiles[profile]
    current.profiles[profile] = existing
      ? [...(Array.isArray(existing) ? existing : [existing]), row]
      : row
    current.profile_account_refs[profile] = { account_scope: accountScope, account_id: accountId }
    current.rows.push(row)
    current.raw_result_row_count += 1
    if (profile === 'BASE') current.base_result_row_count += 1
    else current.stress_result_row_count += 1
    grouped.set(key, current)
  }
  if (grouped.size > 0) return [...grouped.values()]
  return [{
    account_scope: 'MAIN' as const,
    account_group_id: 'SHARED_PAPER',
    account_ids: ['SHARED_PAPER'],
    profiles: opportunity.profiles,
    profile_account_refs: opportunity.profile_account_refs ?? {},
    rows: [],
    raw_result_row_count: opportunity.raw_result_row_count,
    base_result_row_count: opportunity.base_result_row_count,
    stress_result_row_count: opportunity.stress_result_row_count,
    partial_exit_row_count: opportunity.partial_exit_row_count,
  }]
}

function accountGroups(opportunity: TradeOpportunity) {
  return opportunity.account_groups?.length
    ? opportunity.account_groups
    : fallbackAccountGroups(opportunity)
}

function collapseProfileRows(
  opportunity: TradeOpportunity,
  accountGroup: TradeAccountGroup,
  profile: typeof costProfiles[number],
) {
  const value = accountGroup.profiles[profile]
  if (!value) return []
  const rows = Array.isArray(value) ? value : [value]
  const latest = [...rows].sort((left, right) => right.exit_ts_ms - left.exit_ts_ms)[0]
  const representative = rows.find((row) => row.replay_available) ?? latest
  const accountRef = accountGroup.profile_account_refs[profile]
  const fills = mergedFills(rows)
  const fillEvidence = mergedFillEvidence(rows, fills)
  return [{
    ...representative,
    run_id: opportunity.key.run_id,
    opportunity_id: opportunity.key.opportunity_id,
    symbol: opportunity.key.symbol,
    strategy: opportunity.key.strategy_id,
    strategy_display_name_ko: [opportunity.family_label_ko, opportunity.variant_label_ko]
      .filter((label): label is string => Boolean(label?.trim()))
      .join(' · ') || representative.strategy_display_name_ko || null,
    side: opportunity.key.side,
    strategy_version: opportunity.key.strategy_version,
    profile,
    account_scope: accountRef?.account_scope ?? accountGroup.account_scope,
    account_id: accountRef?.account_id ?? representative.account_id ?? accountGroup.account_group_id,
    entry_ts_ms: Math.min(...rows.map((row) => row.entry_ts_ms)),
    exit_ts_ms: Math.max(...rows.map((row) => row.exit_ts_ms)),
    exit: latest.exit,
    quantity: sumRows(rows, 'quantity'),
    gross_pnl: sumRows(rows, 'gross_pnl'),
    fees: sumRows(rows, 'fees'),
    slippage: sumRows(rows, 'slippage'),
    net_pnl: sumRows(rows, 'net_pnl'),
    holding_ms: Math.max(...rows.map((row) => row.holding_ms)),
    holding_seconds: Math.max(...rows.map((row) => row.holding_seconds)),
    replay_available: rows.some((row) => row.replay_available),
    fills,
    ...fillEvidence,
  } satisfies HistoryRow]
}

export function collapseTradeOpportunities(response: TradesResponse) {
  return response.opportunities.flatMap((opportunity) => (
    accountGroups(opportunity).flatMap((accountGroup) => (
      costProfiles.flatMap((profile) => collapseProfileRows(opportunity, accountGroup, profile))
    ))
  ))
}
