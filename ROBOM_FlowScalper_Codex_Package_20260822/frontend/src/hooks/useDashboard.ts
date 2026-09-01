// 초기 연결·WebSocket·PAPER 제어 작업의 상태와 오류를 한 훅에서 관리한다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, fetchJson } from '../api/client'
import { initialDashboard } from '../demoData'
import type {
  ControlAction,
  ControlOperation,
  DashboardData,
  DiagnosticsPayload,
  PageId,
  SettingsSummaryPayload,
  StrategyFamilyCatalogPayload,
  StrategyFamilyDetail,
  StrategyPageSummaryPayload,
  StrategySummaryRow,
  UiChartDelta,
  UiSelectedDetailDelta,
  UiSelectedFamilyDetail,
  UiStrategyRowDelta,
  UiStrategyStateRow,
  UiSummaryPayload,
  UiWebSocketClientMessage,
  UiWebSocketServerMessage,
} from '../types'

type ImmediateAction = 'emergency-close'
type LongAction = 'new-run' | 'start-live' | 'start-demo'
export type DashboardControlAction = ImmediateAction | LongAction
type ConnectionState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING'
type BootstrapState = 'LOADING' | 'READY' | 'ERROR'
type UnknownRecord = Record<string, unknown>

const falseSafetyFields = [
  'real_orders_enabled',
  'auth_required',
  'private_api_enabled',
  'api_key_enabled',
  'wallet_enabled',
  'runtime_ai_order_decision_enabled',
] as const

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null
}

function hasCompletePaperSafetyContract(value: unknown): boolean {
  const candidate = asRecord(value)
  if (!candidate) return false
  const topLevelSafe = candidate.paper_only === true
    && falseSafetyFields.every((field) => candidate[field] === false)
    && candidate.funding_readiness === 'NOT_READY'
  const status = asRecord(candidate.status)
  const risk = asRecord(candidate.risk)
  const system = asRecord(candidate.system)
  const nestedSafe = status?.execution_state === 'PAPER'
    && status.real_orders_enabled === false
    && status.auth_required === false
    && risk?.paper_only === true
    && falseSafetyFields
      .filter((field) => !['real_orders_enabled', 'auth_required'].includes(field))
      .every((field) => system?.[field] === false)
    && system?.funding_readiness === 'NOT_READY'
  return topLevelSafe || nestedSafe
}

function hasCompleteFlatPaperSafetyContract(value: unknown): boolean {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && candidate.paper_only === true
    && falseSafetyFields.every((field) => candidate[field] === false)
    && candidate.funding_readiness === 'NOT_READY',
  )
}

function isSafeUiSummaryDelta(value: unknown): boolean {
  const candidate = asRecord(value)
  if (!candidate) return false
  if ('schema_version' in candidate && candidate.schema_version !== 1) return false
  if ('paper_only' in candidate && candidate.paper_only !== true) return false
  for (const field of falseSafetyFields) {
    if (field in candidate && candidate[field] !== false) return false
  }
  if ('funding_readiness' in candidate && candidate.funding_readiness !== 'NOT_READY') return false
  const status = asRecord(candidate.status)
  if ('status' in candidate && !status) return false
  if (status) {
    if ('execution_state' in status && status.execution_state !== 'PAPER') return false
    if ('real_orders_enabled' in status && status.real_orders_enabled !== false) return false
    if ('auth_required' in status && status.auth_required !== false) return false
  }
  const risk = asRecord(candidate.risk)
  if ('risk' in candidate && !risk) return false
  if (risk && 'paper_only' in risk && risk.paper_only !== true) return false
  const system = asRecord(candidate.system)
  if ('system' in candidate && !system) return false
  if (system) {
    for (const field of falseSafetyFields) {
      if (field in system && system[field] !== false) return false
    }
    if ('funding_readiness' in system && system.funding_readiness !== 'NOT_READY') return false
  }
  if ('paper_entry_intent' in candidate && !asRecord(candidate.paper_entry_intent)) return false
  if ('strategy_state' in candidate && !Array.isArray(candidate.strategy_state)) return false
  for (const field of [
    'main_pending_entry_count',
    'league_pending_entry_count',
    'total_pending_entry_count',
    'total_open_position_count',
  ]) {
    if (field in candidate && (
      typeof candidate[field] !== 'number'
      || !Number.isInteger(candidate[field])
      || Number(candidate[field]) < 0
    )) return false
  }
  if ('paper_portfolio_flat' in candidate && typeof candidate.paper_portfolio_flat !== 'boolean') return false
  return true
}

const terminalStates = new Set([
  'COMPLETED',
  'FAILED_RETRYABLE',
  'FAILED_BLOCKED',
  'CANCELLED',
])

const actionNames: Record<LongAction, ControlAction> = {
  'start-live': 'START_LIVE',
  'start-demo': 'START_DEMO',
  'new-run': 'NEW_RUN',
}

const endpointByAction: Record<ControlAction, LongAction> = {
  START_LIVE: 'start-live',
  START_DEMO: 'start-demo',
  NEW_RUN: 'new-run',
}

function isUiWebSocketServerMessage(value: unknown): value is UiWebSocketServerMessage {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return candidate.schema_version === 1
    && typeof candidate.sequence === 'number'
    && typeof candidate.type === 'string'
    && [
      'snapshot',
      'summary_delta',
      'chart_delta',
      'position_delta',
      'strategy_row_delta',
      'selected_detail_delta',
      'heartbeat',
      'error',
    ].includes(candidate.type)
    && 'data' in candidate
}

function isUiChartDeltaPayload(value: unknown): value is UiChartDelta {
  const candidate = asRecord(value)
  if (!candidate || !isSafeUiSummaryDelta(candidate)) return false
  if (typeof candidate.refresh_required !== 'boolean') return false
  if (candidate.refresh_required) return true
  return typeof candidate.symbol === 'string'
    && typeof candidate.interval === 'string'
    && typeof candidate.fixture === 'boolean'
    && (!('point_upserts' in candidate) || Array.isArray(candidate.point_upserts))
    && (!('removed_point_ts_ms' in candidate) || Array.isArray(candidate.removed_point_ts_ms))
    && (!('candle_upserts' in candidate) || Array.isArray(candidate.candle_upserts))
    && (!('removed_candle_open_ts_ms' in candidate) || Array.isArray(candidate.removed_candle_open_ts_ms))
}

function isUiStrategyStateRow(value: unknown): value is UiStrategyStateRow {
  const candidate = asRecord(value)
  if (!candidate || !isSafeUiSummaryDelta(candidate)) return false
  if (typeof candidate.strategy_id !== 'string' || !candidate.strategy_id.trim()) return false
  if ('mode' in candidate && !['ACTIVE', 'SHADOW', 'OFF'].includes(String(candidate.mode))) return false
  if ('lifecycle' in candidate && ![
    'ACTIVE',
    'CHALLENGER',
    'SHADOW',
    'RESEARCH',
    'RETIRED',
  ].includes(String(candidate.lifecycle))) return false
  for (const field of ['long_enabled', 'short_enabled', 'manual_lock']) {
    if (field in candidate && typeof candidate[field] !== 'boolean') return false
  }
  if ('settings_revision' in candidate && (
    typeof candidate.settings_revision !== 'number'
    || !Number.isInteger(candidate.settings_revision)
    || candidate.settings_revision < 0
  )) return false
  return !('performance' in candidate) || asRecord(candidate.performance) !== null
}

function isUiStrategyRowDeltaPayload(value: unknown): value is UiStrategyRowDelta {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && isSafeUiSummaryDelta(candidate)
    && Array.isArray(candidate.rows)
    && candidate.rows.every(isUiStrategyStateRow)
    && Array.isArray(candidate.removed_strategy_ids)
    && candidate.removed_strategy_ids.every((strategyId) => (
      typeof strategyId === 'string' && strategyId.trim().length > 0
    )),
  )
}

function isUiSelectedDetailDeltaPayload(value: unknown): value is UiSelectedDetailDelta {
  const candidate = asRecord(value)
  if (!candidate || !isSafeUiSummaryDelta(candidate)) return false
  if (candidate.family_id !== null && typeof candidate.family_id !== 'string') return false
  if (candidate.detail === null) return true
  const detail = asRecord(candidate.detail)
  return Boolean(
    detail
    && isSafeUiSummaryDelta(detail)
    && typeof detail.family_id === 'string'
    && detail.family_id === candidate.family_id
    && hasCompleteFlatPaperSafetyContract(detail)
    && Array.isArray(detail.variants),
  )
}

function isUiHeartbeatPayload(value: unknown): value is { server_ts_ms: number } {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && isSafeUiSummaryDelta(candidate)
    && typeof candidate.server_ts_ms === 'number'
    && Number.isFinite(candidate.server_ts_ms),
  )
}

function isUiErrorPayload(value: unknown): value is { error_message_ko: string } {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && isSafeUiSummaryDelta(candidate)
    && typeof candidate.error_message_ko === 'string'
    && candidate.error_message_ko.trim(),
  )
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.messageKo : '프로그램 요청을 처리하지 못했습니다.'
}

function unverifiedSafetyError() {
  return new ApiError({
    code: 'PAPER_SAFETY_NOT_VERIFIED',
    messageKo: 'PAPER 안전 상태를 확인할 때까지 변경 조작을 잠급니다.',
  })
}

function isUiSummaryPayload(value: unknown): value is UiSummaryPayload {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && candidate.status
    && candidate.paper_entry_intent
    && hasCompletePaperSafetyContract(candidate)
    && isSafeUiSummaryDelta(candidate),
  )
}

function isStrategyPageSummaryPayload(value: unknown): value is StrategyPageSummaryPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StrategyPageSummaryPayload>
  return candidate.schema_version === 1
    && hasCompleteFlatPaperSafetyContract(candidate)
    && Array.isArray(candidate.strategies)
    && Array.isArray(candidate.league_accounts)
    && (
      candidate.enabled_directional_entry_candidate_count === undefined
      || (
        Number.isInteger(candidate.enabled_directional_entry_candidate_count)
        && Number(candidate.enabled_directional_entry_candidate_count) >= 0
      )
    )
}

function hasNonNegativeIntegerFields(
  candidate: UnknownRecord,
  fields: readonly string[],
) {
  return fields.every((field) => (
    typeof candidate[field] === 'number'
    && Number.isInteger(candidate[field])
    && Number(candidate[field]) >= 0
  ))
}

function isStrategyInventoryPayload(value: unknown) {
  const candidate = asRecord(value)
  if (!candidate || candidate.schema !== 'flowscalper.strategy_inventory.v1') return false
  const fields = [
    'registered_catalog_item_count',
    'runtime_registry_variant_count',
    'enabled_directional_entry_candidate_count',
    'current_family_entry_representative_count',
    'inactive_history_runtime_variant_count',
    'catalog_virtual_filter_count',
    'active_directional_entry_count',
  ] as const
  if (!hasNonNegativeIntegerFields(candidate, fields)) return false
  return candidate.registered_catalog_item_count
      === Number(candidate.runtime_registry_variant_count) + Number(candidate.catalog_virtual_filter_count)
    && candidate.runtime_registry_variant_count
      === Number(candidate.enabled_directional_entry_candidate_count) + Number(candidate.inactive_history_runtime_variant_count)
    && Number(candidate.active_directional_entry_count)
      <= Number(candidate.enabled_directional_entry_candidate_count)
    && Number(candidate.current_family_entry_representative_count)
      <= Number(candidate.runtime_registry_variant_count)
}

function isV9ResearchManifestPayload(value: unknown) {
  const candidate = asRecord(value)
  if (
    !candidate
    || candidate.schema !== 'flowscalper.v9_candidate_registry.v1'
    || candidate.status !== 'MONITORING_ON_ENTRY_BLOCKED'
    || !hasCompleteFlatPaperSafetyContract(candidate)
    || !Array.isArray(candidate.candidates)
  ) return false
  const fields = [
    'candidate_count',
    'monitoring_on_count',
    'direction_strategy_count',
    'market_neutral_strategy_count',
    'runtime_entry_registered_count',
    'active_count',
    'entry_enabled_count',
  ] as const
  if (!hasNonNegativeIntegerFields(candidate, fields)) return false
  const rows = candidate.candidates.map(asRecord)
  if (rows.some((row) => {
    if (row === null || row.paper_only !== true || !Array.isArray(row.source_ids)) return true
    const sourceIds = row.source_ids
    return sourceIds.length === 0
      || sourceIds.some((sourceId) => typeof sourceId !== 'string' || !sourceId.startsWith('SRC-'))
      || new Set(sourceIds).size !== sourceIds.length
  })) return false
  return Number(candidate.candidate_count) === rows.length
    && Number(candidate.monitoring_on_count) === rows.filter((row) => row?.monitoring_enabled === true).length
    && Number(candidate.direction_strategy_count) === rows.filter((row) => row?.counts_as_direction_strategy === true).length
    && Number(candidate.market_neutral_strategy_count) === rows.filter((row) => row?.counts_as_market_neutral_strategy === true).length
    && Number(candidate.runtime_entry_registered_count) === rows.filter((row) => row?.runtime_entry_registered === true).length
    && Number(candidate.active_count) === rows.filter((row) => row?.active_enabled === true).length
    && Number(candidate.entry_enabled_count) === rows.filter((row) => row?.entry_enabled === true).length
}

function isStrategyFamilyCatalogPayload(value: unknown): value is StrategyFamilyCatalogPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StrategyFamilyCatalogPayload>
  return candidate.schema_version === 1
    && hasCompleteFlatPaperSafetyContract(candidate)
    && Array.isArray(candidate.families)
    && (candidate.inventory === undefined || isStrategyInventoryPayload(candidate.inventory))
    && (candidate.v9_research === undefined || isV9ResearchManifestPayload(candidate.v9_research))
}

function isSettingsSummaryPayload(value: unknown): value is SettingsSummaryPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<SettingsSummaryPayload>
  const safety = asRecord(candidate.safety)
  const paperResearch = asRecord(candidate.paper_research)
  return candidate.schema_version === 1
    && candidate.funding_readiness === 'NOT_READY'
    && safety?.paper_only === true
    && falseSafetyFields.every((field) => safety?.[field] === false)
    && candidate.connection?.public_market_only === true
    && paperResearch?.paper_only === true
    && paperResearch?.real_orders_enabled === false
    && paperResearch?.continuous_entry_mode === true
    && paperResearch?.fees_on_actual_notional === true
    && Array.isArray(paperResearch?.allowed_leverages)
    && Boolean(candidate.run && candidate.safety && candidate.costs && candidate.storage && candidate.connection && candidate.autostart)
}

function isDiagnosticsPayload(value: unknown): value is DiagnosticsPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<DiagnosticsPayload>
  return candidate.schema_version === 1
    && hasCompleteFlatPaperSafetyContract(candidate)
    && Array.isArray(candidate.rows)
    && Boolean(candidate.raw && typeof candidate.raw === 'object' && isSafeUiSummaryDelta(candidate.raw))
}

function isStrategyFamilyDetailPayload(
  value: unknown,
  familyId: string,
): value is StrategyFamilyDetail {
  const candidate = asRecord(value)
  return Boolean(
    candidate
    && candidate.family_id === familyId
    && Array.isArray(candidate.variants)
    && hasCompleteFlatPaperSafetyContract(candidate),
  )
}

function mergeStrategyState(current: StrategySummaryRow, update: UiStrategyStateRow): StrategySummaryRow {
  const performance = update.performance
    ? {
        BASE: { ...current.performance.BASE, ...(update.performance.BASE ?? {}) },
        STRESS: { ...current.performance.STRESS, ...(update.performance.STRESS ?? {}) },
      }
    : current.performance
  return { ...current, ...update, performance }
}

function mergeUiSummary(current: DashboardData, summary: UiSummaryPayload): DashboardData {
  const { strategy_state: strategyState, ...delta } = summary
  const strategyById = new Map(strategyState?.map((row) => [row.strategy_id, row]) ?? [])
  return {
    ...current,
    ...delta,
    status: summary.status ? { ...current.status, ...summary.status } : current.status,
    operation_status: summary.operation_status ? { ...current.operation_status, ...summary.operation_status } : current.operation_status,
    paper_entry_intent: summary.paper_entry_intent ? { ...current.paper_entry_intent, ...summary.paper_entry_intent } : current.paper_entry_intent,
    performance: summary.performance ? { ...current.performance, ...summary.performance } : current.performance,
    system: summary.system ? { ...current.system, ...summary.system } : current.system,
    strategies: strategyState
      ? current.strategies.map((strategy) => {
          const update = strategyById.get(strategy.strategy_id)
          return update ? mergeStrategyState(strategy, update) : strategy
        })
      : current.strategies,
  }
}

function mergeStrategyRowDelta(current: DashboardData, delta: UiStrategyRowDelta): DashboardData {
  const removed = new Set(delta.removed_strategy_ids)
  const byId = new Map(delta.rows.map((row) => [row.strategy_id, row]))
  return {
    ...current,
    strategies: current.strategies
      .filter((strategy) => !removed.has(strategy.strategy_id))
      .map((strategy) => {
        const update = byId.get(strategy.strategy_id)
        return update ? mergeStrategyState(strategy, update) : strategy
      }),
  }
}

function mergeChartDelta(current: DashboardData, delta: UiChartDelta): DashboardData {
  const removedPointTs = new Set(delta.removed_point_ts_ms ?? [])
  const points = new Map(
    current.chart.points
      .filter((point) => !removedPointTs.has(point.ts_ms))
      .map((point) => [point.ts_ms, point]),
  )
  for (const point of delta.point_upserts ?? []) points.set(point.ts_ms, point)

  const removedCandleTs = new Set(delta.removed_candle_open_ts_ms ?? [])
  const candles = new Map(
    current.chart.candles
      .filter((candle) => !removedCandleTs.has(candle.open_ts_ms))
      .map((candle) => [candle.open_ts_ms, candle]),
  )
  for (const candle of delta.candle_upserts ?? []) candles.set(candle.open_ts_ms, candle)
  return {
    ...current,
    chart: {
      ...current.chart,
      symbol: delta.symbol,
      interval: delta.interval,
      fixture: delta.fixture,
      points: [...points.values()].sort((left, right) => left.ts_ms - right.ts_ms),
      candles: [...candles.values()].sort((left, right) => left.open_ts_ms - right.open_ts_ms),
      lines: delta.lines ? { ...current.chart.lines, ...delta.lines } : current.chart.lines,
    },
  }
}

function mergeSettingsSummary(current: DashboardData, summary: SettingsSummaryPayload): DashboardData {
  return {
    ...current,
    settings_summary: summary,
    status: {
      ...current.status,
      run_id: summary.run.run_id,
      mode: summary.run.mode,
      venue: summary.run.venue,
    },
    paper_entry_intent: {
      ...current.paper_entry_intent,
      state: summary.safety.entry_state,
      revision: summary.safety.entry_revision,
    },
    risk: {
      ...current.risk,
      active_locks: summary.safety.active_locks,
      strategy_league: {
        ...current.risk.strategy_league,
        ...summary.costs,
        selected_margin_leverage: `${summary.paper_research.selected_leverage}x`,
      },
    },
    system: {
      ...current.system,
      storage: summary.storage.label ?? current.system.storage,
      disk_free_mb: summary.storage.free_mb ?? current.system.disk_free_mb,
      disk_free_ratio: summary.storage.free_ratio ?? current.system.disk_free_ratio,
      storage_entry_allowed: summary.storage.entry_allowed ?? current.system.storage_entry_allowed,
      storage_lock_reason: summary.storage.lock_reason ?? current.system.storage_lock_reason,
      connection_state: summary.connection.state ?? current.system.connection_state,
      funding_readiness: summary.funding_readiness,
    },
  }
}

export function useDashboard(page: PageId = 'market') {
  const [data, setData] = useState<DashboardData>(initialDashboard)
  const [connected, setConnected] = useState(false)
  const [safetyVerified, setSafetyVerified] = useState(false)
  const [connectionState, setConnectionState] = useState<ConnectionState>('CONNECTING')
  const [bootstrapState, setBootstrapState] = useState<BootstrapState>('LOADING')
  const [lastUpdateMs, setLastUpdateMs] = useState<number | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [requestError, setRequestError] = useState('')
  const [busyAction, setBusyAction] = useState<LongAction | null>(null)
  const [submittedOperation, setSubmittedOperation] = useState<ControlOperation | null>(null)
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null)
  const [selectedFamilyDetail, setSelectedFamilyDetail] = useState<UiSelectedFamilyDetail | null>(null)
  const idempotencyKeys = useRef(new Map<LongAction, string>())
  const hasConnected = useRef(false)
  const mounted = useRef(true)
  const uiSocket = useRef<WebSocket | null>(null)
  const uiSequence = useRef(0)
  const selectedFamilyIdRef = useRef<string | null>(null)
  const strategyStateById = useRef(new Map<string, UiStrategyStateRow>())
  const safetyEpoch = useRef(0)

  const invalidateSafety = useCallback(() => {
    safetyEpoch.current += 1
    setSafetyVerified(false)
  }, [])

  const applyUiSummary = useCallback((summary: UiSummaryPayload) => {
    if (!mounted.current) return
    if (hasCompletePaperSafetyContract(summary)) setSafetyVerified(true)
    for (const row of summary.strategy_state ?? []) {
      strategyStateById.current.set(row.strategy_id, row)
    }
    setData((current) => mergeUiSummary(current, summary))
    setLastUpdateMs(Date.now())
    setBootstrapState('READY')
    setConnectionError('')
    if (summary.control_operation) {
      setSubmittedOperation(summary.control_operation)
      if (terminalStates.has(summary.control_operation.state)) {
        setBusyAction(null)
        idempotencyKeys.current.delete(endpointByAction[summary.control_operation.action])
      }
    }
  }, [])

  const refreshUiSummary = useCallback(async (signal?: AbortSignal) => {
    const requestSafetyEpoch = safetyEpoch.current
    const summary = await fetchJson<UiSummaryPayload>(
      '/api/ui/summary',
      signal ? { signal, cache: 'no-store' } : { cache: 'no-store' },
      10_000,
    )
    if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
    if (!isUiSummaryPayload(summary)) {
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        messageKo: '프로그램 서버의 화면 요약이 올바르지 않습니다.',
      })
    }
    applyUiSummary(summary)
    return summary
  }, [applyUiSummary])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    let socket: WebSocket | undefined
    let reconnectTimer: number | undefined
    let disposed = false

    const connect = () => {
      if (!('WebSocket' in window) || disposed) return
      setConnectionState(hasConnected.current ? 'RECONNECTING' : 'CONNECTING')
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const currentSocket = new WebSocket(`${protocol}//${window.location.host}/ws/ui`)
      socket = currentSocket
      uiSocket.current = currentSocket
      currentSocket.addEventListener('open', () => {
        if (disposed || uiSocket.current !== currentSocket) return
        uiSequence.current = 0
        hasConnected.current = true
        setConnected(true)
        setConnectionState('CONNECTED')
        setConnectionError('')
        if (selectedFamilyIdRef.current && currentSocket.readyState === 1) {
          const request: UiWebSocketClientMessage = {
            type: 'select_family',
            family_id: selectedFamilyIdRef.current,
          }
          currentSocket.send(JSON.stringify(request))
        }
      })
      currentSocket.addEventListener('message', (event) => {
        if (disposed || uiSocket.current !== currentSocket) return
        try {
          const parsed = JSON.parse(String(event.data)) as unknown
          if (!isUiWebSocketServerMessage(parsed)) throw new Error('malformed V6 UI envelope')
          const payload = parsed
          if (payload.sequence <= uiSequence.current) return
          uiSequence.current = payload.sequence
          switch (payload.type) {
              case 'snapshot':
                if (!isUiSummaryPayload(payload.data)) throw new Error('unsafe V6 UI snapshot')
                applyUiSummary(payload.data)
                break
              case 'summary_delta':
              case 'position_delta':
                if (!isSafeUiSummaryDelta(payload.data)) throw new Error('unsafe V6 UI delta')
                applyUiSummary(payload.data)
                break
              case 'chart_delta':
                if (!isUiChartDeltaPayload(payload.data)) throw new Error('unsafe V6 chart delta')
                if (payload.data.refresh_required) {
                  void refreshUiSummary().catch((error: unknown) => {
                    if (mounted.current) setConnectionError(errorMessage(error))
                  })
                } else {
                  setData((current) => mergeChartDelta(current, payload.data))
                  setLastUpdateMs(Date.now())
                }
                break
              case 'strategy_row_delta':
                if (!isUiStrategyRowDeltaPayload(payload.data)) throw new Error('unsafe V6 strategy delta')
                for (const strategyId of payload.data.removed_strategy_ids) {
                  strategyStateById.current.delete(strategyId)
                }
                for (const row of payload.data.rows) {
                  strategyStateById.current.set(row.strategy_id, row)
                }
                setData((current) => mergeStrategyRowDelta(current, payload.data))
                setLastUpdateMs(Date.now())
                break
              case 'selected_detail_delta':
                if (!isUiSelectedDetailDeltaPayload(payload.data)) throw new Error('unsafe V6 detail delta')
                if (payload.data.family_id === selectedFamilyIdRef.current) {
                  setSelectedFamilyDetail(payload.data.detail)
                  setLastUpdateMs(Date.now())
                }
                break
              case 'heartbeat':
                if (!isUiHeartbeatPayload(payload.data)) throw new Error('unsafe V6 heartbeat')
                setLastUpdateMs(Date.now())
                break
              case 'error':
                if (!isUiErrorPayload(payload.data)) throw new Error('unsafe V6 error')
                setRequestError(payload.data.error_message_ko)
                break
            default:
              throw new Error('unsupported V6 UI message')
          }
          setConnected(true)
          setConnectionState('CONNECTED')
        } catch {
          invalidateSafety()
          setConnected(false)
          setConnectionState('RECONNECTING')
          setConnectionError('화면 데이터를 다시 연결하고 있습니다.')
          currentSocket.close()
        }
      })
      currentSocket.addEventListener('close', () => {
        if (disposed || uiSocket.current !== currentSocket) return
        uiSocket.current = null
        invalidateSafety()
        setConnected(false)
        setConnectionState('RECONNECTING')
        setConnectionError('프로그램 화면 연결이 끊겨 다시 연결하고 있습니다.')
        reconnectTimer = window.setTimeout(connect, 1_000)
      })
      currentSocket.addEventListener('error', () => currentSocket.close())
    }

    const initialSafetyEpoch = safetyEpoch.current
    void fetchJson<UiSummaryPayload>('/api/ui/summary', { signal: controller.signal }, 10_000)
      .then((summary) => {
        if (initialSafetyEpoch !== safetyEpoch.current) return
        if (!isUiSummaryPayload(summary)) {
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '프로그램 서버의 화면 요약이 올바르지 않습니다.',
          })
        }
        applyUiSummary(summary)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || disposed) return
        invalidateSafety()
        setBootstrapState('ERROR')
        setConnectionError(errorMessage(error))
      })
    connect()

    return () => {
      disposed = true
      mounted.current = false
      controller.abort()
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
      if (uiSocket.current === socket) uiSocket.current = null
    }
  }, [applyUiSummary, invalidateSafety, refreshUiSummary])

  useEffect(() => {
    if (page !== 'market' && page !== 'strategies' && page !== 'settings') return
    const controller = new AbortController()
    const requestSafetyEpoch = safetyEpoch.current
    if (page === 'market' || page === 'strategies') {
      void (async () => {
        const summary = await fetchJson<StrategyPageSummaryPayload>(
          '/api/strategies/summary',
          { signal: controller.signal },
          10_000,
        )
        if (!isStrategyPageSummaryPayload(summary)) {
          invalidateSafety()
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '전략 화면 요약이 올바르지 않습니다.',
          })
        }
        const catalog = page === 'strategies'
          ? await fetchJson<StrategyFamilyCatalogPayload>(
              '/api/strategy-families',
              { signal: controller.signal },
              10_000,
            )
          : null
        if (catalog && !isStrategyFamilyCatalogPayload(catalog)) {
          invalidateSafety()
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '전략 family 목록이 올바르지 않습니다.',
          })
        }
        if (!mounted.current || requestSafetyEpoch !== safetyEpoch.current) return
        setData((current) => ({
          ...current,
          strategies: summary.strategies.map((strategy) => {
            const update = strategyStateById.current.get(strategy.strategy_id)
            return update ? mergeStrategyState(strategy, update) : strategy
          }),
          league_accounts: summary.league_accounts,
          strategy_family_catalog: catalog ?? current.strategy_family_catalog,
          enabled_directional_entry_candidate_count: (
            summary.enabled_directional_entry_candidate_count
            ?? current.enabled_directional_entry_candidate_count
          ),
        }))
      })().catch((error: unknown) => {
        if (!controller.signal.aborted && mounted.current) setRequestError(errorMessage(error))
      })
    } else {
      void Promise.all([
        fetchJson<SettingsSummaryPayload>(
          '/api/settings/summary',
          { signal: controller.signal },
          10_000,
        ),
        fetchJson<DiagnosticsPayload>(
          '/api/diagnostics',
          { signal: controller.signal },
          10_000,
        ),
      ]).then(([summary, diagnostics]) => {
        if (!isSettingsSummaryPayload(summary)) {
          invalidateSafety()
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '설정 화면 요약이 올바르지 않습니다.',
          })
        }
        if (!isDiagnosticsPayload(diagnostics)) {
          invalidateSafety()
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '전문가 진단 정보가 올바르지 않습니다.',
          })
        }
        if (mounted.current && requestSafetyEpoch === safetyEpoch.current) {
          setData((current) => {
            const merged = mergeSettingsSummary(current, summary)
            return {
              ...merged,
              diagnostics,
              system: { ...merged.system, ...diagnostics.raw },
            }
          })
        }
      }).catch((error: unknown) => {
        if (!controller.signal.aborted && mounted.current) setRequestError(errorMessage(error))
      })
    }
    return () => controller.abort()
  }, [invalidateSafety, page])

  const updateUiSummary = useCallback(async (path: string, init: RequestInit) => {
    if (!connected || !safetyVerified) {
      const error = unverifiedSafetyError()
      if (mounted.current) setRequestError(error.messageKo)
      throw error
    }
    const requestSafetyEpoch = safetyEpoch.current
    const summary = await fetchJson<UiSummaryPayload>(path, init)
    if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
    if (!isUiSummaryPayload(summary)) {
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        messageKo: '프로그램 서버의 변경 응답이 올바르지 않습니다.',
      })
    }
    applyUiSummary(summary)
    return summary
  }, [applyUiSummary, connected, safetyVerified])

  const submitLongControl = useCallback(async (action: LongAction) => {
    if (!connected || !safetyVerified) {
      const error = unverifiedSafetyError()
      if (mounted.current) setRequestError(error.messageKo)
      throw error
    }
    if (busyAction) return submittedOperation
    setBusyAction(action)
    setRequestError('')
    try {
      const requestSafetyEpoch = safetyEpoch.current
      const idempotencyKey = idempotencyKeys.current.get(action) ?? crypto.randomUUID()
      idempotencyKeys.current.set(action, idempotencyKey)
      const operation = await fetchJson<ControlOperation>(
        `/api/control/${action}`,
        {
          method: 'POST',
          headers: { 'Idempotency-Key': idempotencyKey },
          body: JSON.stringify({
            expected_revision: data.control_revision,
            reason: `USER_${action.toUpperCase().replaceAll('-', '_')}`,
          }),
        },
      )
      if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
      if (!mounted.current) return operation
      setSubmittedOperation(operation)
      if (terminalStates.has(operation.state)) setBusyAction(null)
      return operation
    } catch (error) {
      if (mounted.current) {
        setBusyAction(null)
        setRequestError(errorMessage(error))
      }
      throw error
    }
  }, [busyAction, connected, data.control_revision, safetyVerified, submittedOperation])

  const control = useCallback(async (action: DashboardControlAction) => {
    if (action in actionNames) return submitLongControl(action as LongAction)
    setRequestError('')
    try {
      return await updateUiSummary(`/api/control/${action}`, { method: 'POST' })
    } catch (error) {
      if (mounted.current) setRequestError(errorMessage(error))
      throw error
    }
  }, [submitLongControl, updateUiSummary])

  const cancelControl = useCallback(async () => {
    if (!connected || !safetyVerified) {
      const error = unverifiedSafetyError()
      if (mounted.current) setRequestError(error.messageKo)
      throw error
    }
    const operation = data.control_operation ?? submittedOperation
    if (!operation || terminalStates.has(operation.state)) return operation
    setRequestError('')
    try {
      const requestSafetyEpoch = safetyEpoch.current
      const cancelled = await fetchJson<ControlOperation>(
        `/api/control/operations/${encodeURIComponent(operation.operation_id)}/cancel`,
        { method: 'POST' },
      )
      if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
      if (mounted.current) setSubmittedOperation(cancelled)
      return cancelled
    } catch (error) {
      if (mounted.current) setRequestError(errorMessage(error))
      throw error
    }
  }, [connected, data.control_operation, safetyVerified, submittedOperation])

  const retryControl = useCallback(() => {
    const operation = data.control_operation ?? submittedOperation
    if (!operation?.retryable) return Promise.resolve(null)
    return submitLongControl(endpointByAction[operation.action])
  }, [data.control_operation, submitLongControl, submittedOperation])

  const selectChart = useCallback(
    async (symbol: string, intervalSeconds: number) => {
      await updateUiSummary('/api/control/chart', {
        method: 'POST',
        body: JSON.stringify({ symbol, interval_seconds: intervalSeconds }),
      })
      return refreshUiSummary()
    },
    [refreshUiSummary, updateUiSummary],
  )

  const configureStrategy = useCallback(
    (
      strategyId: string,
      configuration: { mode: 'ACTIVE' | 'SHADOW' | 'OFF'; long_enabled: boolean; short_enabled: boolean; expected_revision: number },
    ) =>
      updateUiSummary(`/api/strategies/${encodeURIComponent(strategyId)}`, {
        method: 'POST',
        body: JSON.stringify({
          ...configuration,
          manual_lock: true,
          reason: 'USER_CONFIGURATION',
        }),
      }),
    [updateUiSummary],
  )

  const configurePaperResearch = useCallback(
    async (selectedLeverage: number, expectedRevision: number) => {
      if (!connected || !safetyVerified) {
        const error = unverifiedSafetyError()
        if (mounted.current) setRequestError(error.messageKo)
        throw error
      }
      setRequestError('')
      try {
        const requestSafetyEpoch = safetyEpoch.current
        const summary = await fetchJson<SettingsSummaryPayload>(
          '/api/settings/paper-research',
          {
            method: 'POST',
            body: JSON.stringify({
              selected_leverage: selectedLeverage,
              expected_revision: expectedRevision,
              reason: 'USER_PAPER_LEVERAGE_CONFIGURATION',
            }),
          },
        )
        if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
        if (!isSettingsSummaryPayload(summary)) {
          invalidateSafety()
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '변경된 PAPER 배수 설정을 안전하게 확인하지 못했습니다.',
          })
        }
        if (mounted.current) setData((current) => mergeSettingsSummary(current, summary))
        return summary
      } catch (error) {
        if (mounted.current) setRequestError(errorMessage(error))
        throw error
      }
    },
    [connected, invalidateSafety, safetyVerified],
  )

  const rollbackStrategy = useCallback(
    (strategyId: string, targetRevision: number, expectedRevision: number) =>
      updateUiSummary(`/api/strategies/${encodeURIComponent(strategyId)}/rollback`, {
        method: 'POST',
        body: JSON.stringify({
          target_revision: targetRevision,
          expected_revision: expectedRevision,
          reason: `USER_ROLLBACK_TO_REV_${targetRevision}`,
        }),
      }),
    [updateUiSummary],
  )

  const configureStrategyFamilyResearch = useCallback(async (
    familyId: string,
    configuration: {
      research_enabled: boolean
      expected_revision: number
      reason: string
    },
  ) => {
    if (!connected || !safetyVerified) {
      const error = unverifiedSafetyError()
      if (mounted.current) setRequestError(error.messageKo)
      throw error
    }
    const requestSafetyEpoch = safetyEpoch.current
    const detail = await fetchJson<StrategyFamilyDetail>(
      `/api/strategy-families/${encodeURIComponent(familyId)}/research-enabled`,
      {
        method: 'PATCH',
        body: JSON.stringify(configuration),
      },
    )
    if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
    if (!isStrategyFamilyDetailPayload(detail, familyId)) {
      invalidateSafety()
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        messageKo: '전략 family 변경 응답의 PAPER 안전 상태가 올바르지 않습니다.',
      })
    }
    try {
      const catalog = await fetchJson<StrategyFamilyCatalogPayload>(
        '/api/strategy-families',
        { cache: 'no-store' },
        10_000,
      )
      if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
      if (!isStrategyFamilyCatalogPayload(catalog)) {
        invalidateSafety()
        if (mounted.current) {
          setRequestError('전략 설정은 저장됐지만 변경 후 수량 요약의 PAPER 계약을 확인할 수 없습니다.')
        }
      } else if (mounted.current) {
        setData((current) => ({ ...current, strategy_family_catalog: catalog }))
      }
    } catch {
      if (requestSafetyEpoch !== safetyEpoch.current) throw unverifiedSafetyError()
      if (mounted.current) {
        setRequestError('전략 설정은 저장됐지만 전체 수량을 새로고침하지 못했습니다.')
      }
    }
    return detail
  }, [connected, invalidateSafety, safetyVerified])

  const selectStrategyFamily = useCallback((familyId: string | null) => {
    selectedFamilyIdRef.current = familyId
    setSelectedFamilyId(familyId)
    setSelectedFamilyDetail(null)
    setRequestError('')
    const request: UiWebSocketClientMessage = {
      type: 'select_family',
      family_id: familyId,
    }
    if (uiSocket.current?.readyState === 1) {
      uiSocket.current.send(JSON.stringify(request))
    }
  }, [])

  const clearError = useCallback(() => {
    setConnectionError('')
    setRequestError('')
  }, [])

  return {
    data,
    connected,
    safetyVerified,
    connectionState,
    bootstrapState,
    lastUpdateMs,
    connectionError,
    requestError,
    busyAction,
    controlOperation: data.control_operation ?? submittedOperation,
    control,
    cancelControl,
    retryControl,
    refreshUiSummary,
    selectChart,
    configureStrategy,
    configurePaperResearch,
    rollbackStrategy,
    configureStrategyFamilyResearch,
    selectedFamilyId,
    selectedFamilyDetail,
    selectStrategyFamily,
    clearError,
  }
}
