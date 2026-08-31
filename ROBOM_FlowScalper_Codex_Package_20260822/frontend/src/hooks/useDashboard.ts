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
  StrategyPageSummaryPayload,
  StrategySummaryRow,
  UiChartDelta,
  UiSelectedFamilyDetail,
  UiStrategyRowDelta,
  UiStrategyStateRow,
  UiSummaryPayload,
  UiWebSocketClientMessage,
  UiWebSocketServerMessage,
} from '../types'

type ImmediateAction = 'pause' | 'resume' | 'emergency-close'
type LongAction = 'new-run' | 'start-live' | 'start-demo'
export type DashboardControlAction = ImmediateAction | LongAction
type ConnectionState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING'
type BootstrapState = 'LOADING' | 'READY' | 'ERROR'

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

function isDashboardData(value: unknown): value is DashboardData {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<DashboardData>
  return Boolean(
    candidate.status
    && Array.isArray(candidate.scanner)
    && Array.isArray(candidate.strategies)
    && Array.isArray(candidate.league_accounts)
    && Array.isArray(candidate.league_positions),
  )
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

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.messageKo : '프로그램 요청을 처리하지 못했습니다.'
}

function isUiSummaryPayload(value: unknown): value is UiSummaryPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as UiSummaryPayload
  return Boolean(candidate.status && candidate.paper_entry_intent)
}

function isStrategyPageSummaryPayload(value: unknown): value is StrategyPageSummaryPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StrategyPageSummaryPayload>
  return candidate.schema_version === 1
    && candidate.paper_only === true
    && candidate.real_orders_enabled === false
    && candidate.auth_required === false
    && Array.isArray(candidate.strategies)
    && Array.isArray(candidate.league_accounts)
}

function isStrategyFamilyCatalogPayload(value: unknown): value is StrategyFamilyCatalogPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StrategyFamilyCatalogPayload>
  return candidate.schema_version === 1
    && candidate.paper_only === true
    && candidate.real_orders_enabled === false
    && candidate.auth_required === false
    && Array.isArray(candidate.families)
}

function isSettingsSummaryPayload(value: unknown): value is SettingsSummaryPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<SettingsSummaryPayload>
  return candidate.schema_version === 1
    && candidate.funding_readiness === 'NOT_READY'
    && Boolean(candidate.run && candidate.safety && candidate.costs && candidate.storage && candidate.connection && candidate.autostart)
}

function isDiagnosticsPayload(value: unknown): value is DiagnosticsPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<DiagnosticsPayload>
  return candidate.schema_version === 1
    && candidate.paper_only === true
    && candidate.real_orders_enabled === false
    && candidate.auth_required === false
    && Array.isArray(candidate.rows)
    && Boolean(candidate.raw && typeof candidate.raw === 'object')
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
      strategy_league: { ...current.risk.strategy_league, ...summary.costs },
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
  const [connectionState, setConnectionState] = useState<ConnectionState>('CONNECTING')
  const [bootstrapState, setBootstrapState] = useState<BootstrapState>('LOADING')
  const [lastUpdateMs, setLastUpdateMs] = useState<number | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [requestError, setRequestError] = useState('')
  const [busyAction, setBusyAction] = useState<LongAction | null>(null)
  const [immediateBusyAction, setImmediateBusyAction] = useState<'pause' | 'resume' | null>(null)
  const [submittedOperation, setSubmittedOperation] = useState<ControlOperation | null>(null)
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null)
  const [selectedFamilyDetail, setSelectedFamilyDetail] = useState<UiSelectedFamilyDetail | null>(null)
  const idempotencyKeys = useRef(new Map<LongAction, string>())
  const immediateIdempotencyKeys = useRef(new Map<'pause' | 'resume', string>())
  const hasConnected = useRef(false)
  const mounted = useRef(true)
  const uiSocket = useRef<WebSocket | null>(null)
  const uiSequence = useRef(0)
  const selectedFamilyIdRef = useRef<string | null>(null)
  const strategyStateById = useRef(new Map<string, UiStrategyStateRow>())

  const applySnapshot = useCallback((snapshot: DashboardData) => {
    if (!mounted.current) return
    setData(snapshot)
    setLastUpdateMs(Date.now())
    setBootstrapState('READY')
    setConnectionError('')
    if (snapshot.control_operation) {
      setSubmittedOperation(snapshot.control_operation)
      if (terminalStates.has(snapshot.control_operation.state)) {
        setBusyAction(null)
        idempotencyKeys.current.delete(endpointByAction[snapshot.control_operation.action])
      }
    }
  }, [])

  const applyUiSummary = useCallback((summary: UiSummaryPayload) => {
    if (!mounted.current) return
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
    const summary = await fetchJson<UiSummaryPayload>(
      '/api/ui/summary',
      signal ? { signal } : {},
      10_000,
    )
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
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/ui`)
      uiSocket.current = socket
      socket.addEventListener('open', () => {
        if (disposed) return
        uiSequence.current = 0
        hasConnected.current = true
        setConnected(true)
        setConnectionState('CONNECTED')
        setConnectionError('')
        if (selectedFamilyIdRef.current && socket?.readyState === 1) {
          const request: UiWebSocketClientMessage = {
            type: 'select_family',
            family_id: selectedFamilyIdRef.current,
          }
          socket.send(JSON.stringify(request))
        }
      })
      socket.addEventListener('message', (event) => {
        if (disposed) return
        try {
          const parsed = JSON.parse(String(event.data)) as unknown
          const legacy = parsed && typeof parsed === 'object'
            ? parsed as { type?: unknown; data?: unknown }
            : null
          if (legacy?.type === 'dashboard' && isDashboardData(legacy.data)) applySnapshot(legacy.data)
          else if (legacy?.type === 'ui_summary' && legacy.data && typeof legacy.data === 'object') applyUiSummary(legacy.data as UiSummaryPayload)
          else {
            if (!isUiWebSocketServerMessage(parsed)) throw new Error('malformed V6 UI envelope')
            const payload = parsed
            if (payload.sequence <= uiSequence.current) return
            uiSequence.current = payload.sequence
            switch (payload.type) {
              case 'snapshot':
              case 'summary_delta':
              case 'position_delta':
                applyUiSummary(payload.data)
                break
              case 'chart_delta':
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
                if (payload.data.family_id === selectedFamilyIdRef.current) {
                  setSelectedFamilyDetail(payload.data.detail)
                  setLastUpdateMs(Date.now())
                }
                break
              case 'heartbeat':
                setLastUpdateMs(Date.now())
                break
              case 'error':
                setRequestError(payload.data.error_message_ko)
                break
              default:
                throw new Error('unsupported V6 UI message')
            }
          }
          setConnected(true)
          setConnectionState('CONNECTED')
        } catch {
          setConnected(false)
          setConnectionState('RECONNECTING')
          setConnectionError('화면 데이터를 다시 연결하고 있습니다.')
          socket?.close()
        }
      })
      socket.addEventListener('close', () => {
        if (disposed) return
        if (uiSocket.current === socket) uiSocket.current = null
        setConnected(false)
        setConnectionState('RECONNECTING')
        setConnectionError('프로그램 화면 연결이 끊겨 다시 연결하고 있습니다.')
        reconnectTimer = window.setTimeout(connect, 1_000)
      })
      socket.addEventListener('error', () => socket?.close())
    }

    void fetchJson<UiSummaryPayload>('/api/ui/summary', { signal: controller.signal }, 10_000)
      .then((summary) => {
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
  }, [applySnapshot, applyUiSummary, refreshUiSummary])

  useEffect(() => {
    if (page !== 'market' && page !== 'strategies' && page !== 'settings') return
    const controller = new AbortController()
    if (page === 'market' || page === 'strategies') {
      void (async () => {
        const summary = await fetchJson<StrategyPageSummaryPayload>(
          '/api/strategies/summary',
          { signal: controller.signal },
          10_000,
        )
        if (!isStrategyPageSummaryPayload(summary)) {
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
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '전략 family 목록이 올바르지 않습니다.',
          })
        }
        if (!mounted.current) return
        setData((current) => ({
          ...current,
          strategies: summary.strategies.map((strategy) => {
            const update = strategyStateById.current.get(strategy.strategy_id)
            return update ? mergeStrategyState(strategy, update) : strategy
          }),
          league_accounts: summary.league_accounts,
          strategy_family_catalog: catalog ?? current.strategy_family_catalog,
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
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '설정 화면 요약이 올바르지 않습니다.',
          })
        }
        if (!isDiagnosticsPayload(diagnostics)) {
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '전문가 진단 정보가 올바르지 않습니다.',
          })
        }
        if (mounted.current) {
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
  }, [page])

  const updateUiSummary = useCallback(async (path: string, init: RequestInit) => {
    const summary = await fetchJson<UiSummaryPayload>(path, init)
    if (!isUiSummaryPayload(summary)) {
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        messageKo: '프로그램 서버의 변경 응답이 올바르지 않습니다.',
      })
    }
    applyUiSummary(summary)
    return summary
  }, [applyUiSummary])

  const submitLongControl = useCallback(async (action: LongAction) => {
    if (busyAction) return submittedOperation
    setBusyAction(action)
    setRequestError('')
    try {
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
  }, [busyAction, data.control_revision, submittedOperation])

  const control = useCallback(async (action: DashboardControlAction) => {
    if (action in actionNames) return submitLongControl(action as LongAction)
    setRequestError('')
    try {
      if (action === 'pause' || action === 'resume') {
        setImmediateBusyAction(action)
        const idempotencyKey = immediateIdempotencyKeys.current.get(action) ?? crypto.randomUUID()
        immediateIdempotencyKeys.current.set(action, idempotencyKey)
        try {
          const snapshot = await updateUiSummary(`/api/control/${action}`, {
            method: 'POST',
            headers: { 'Idempotency-Key': idempotencyKey },
            body: JSON.stringify({
              expected_revision: data.paper_entry_intent.revision,
              reason: action === 'pause' ? 'USER_PAUSE' : 'USER_RESUME',
            }),
          })
          immediateIdempotencyKeys.current.delete(action)
          return snapshot
        } finally {
          if (mounted.current) setImmediateBusyAction(null)
        }
      }
      return await updateUiSummary(`/api/control/${action}`, { method: 'POST' })
    } catch (error) {
      if (mounted.current) setRequestError(errorMessage(error))
      throw error
    }
  }, [data.paper_entry_intent.revision, submitLongControl, updateUiSummary])

  const cancelControl = useCallback(async () => {
    const operation = data.control_operation ?? submittedOperation
    if (!operation || terminalStates.has(operation.state)) return operation
    setRequestError('')
    try {
      const cancelled = await fetchJson<ControlOperation>(
        `/api/control/operations/${encodeURIComponent(operation.operation_id)}/cancel`,
        { method: 'POST' },
      )
      if (mounted.current) setSubmittedOperation(cancelled)
      return cancelled
    } catch (error) {
      if (mounted.current) setRequestError(errorMessage(error))
      throw error
    }
  }, [data.control_operation, submittedOperation])

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
    connectionState,
    bootstrapState,
    lastUpdateMs,
    connectionError,
    requestError,
    busyAction,
    immediateBusyAction,
    controlOperation: data.control_operation ?? submittedOperation,
    control,
    cancelControl,
    retryControl,
    selectChart,
    configureStrategy,
    rollbackStrategy,
    selectedFamilyId,
    selectedFamilyDetail,
    selectStrategyFamily,
    clearError,
  }
}
