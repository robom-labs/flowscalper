// 초기 연결·WebSocket·PAPER 제어 작업의 상태와 오류를 한 훅에서 관리한다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, fetchJson } from '../api/client'
import { initialDashboard } from '../demoData'
import type { ControlAction, ControlOperation, DashboardData } from '../types'

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

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.messageKo : '프로그램 요청을 처리하지 못했습니다.'
}

export function useDashboard() {
  const [data, setData] = useState<DashboardData>(initialDashboard)
  const [connected, setConnected] = useState(false)
  const [connectionState, setConnectionState] = useState<ConnectionState>('CONNECTING')
  const [bootstrapState, setBootstrapState] = useState<BootstrapState>('LOADING')
  const [lastUpdateMs, setLastUpdateMs] = useState<number | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [requestError, setRequestError] = useState('')
  const [busyAction, setBusyAction] = useState<LongAction | null>(null)
  const [submittedOperation, setSubmittedOperation] = useState<ControlOperation | null>(null)
  const idempotencyKeys = useRef(new Map<LongAction, string>())
  const hasConnected = useRef(false)
  const mounted = useRef(true)

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
      socket = new WebSocket(`${protocol}//${window.location.host}/ws`)
      socket.addEventListener('open', () => {
        if (disposed) return
        hasConnected.current = true
        setConnected(true)
        setConnectionState('CONNECTED')
        setConnectionError('')
      })
      socket.addEventListener('message', (event) => {
        if (disposed) return
        try {
          const payload = JSON.parse(String(event.data)) as { type?: unknown; data?: unknown }
          if (payload.type !== 'dashboard' || !isDashboardData(payload.data)) {
            throw new Error('malformed dashboard payload')
          }
          applySnapshot(payload.data)
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
        setConnected(false)
        setConnectionState('RECONNECTING')
        setConnectionError('프로그램 화면 연결이 끊겨 다시 연결하고 있습니다.')
        reconnectTimer = window.setTimeout(connect, 1_000)
      })
      socket.addEventListener('error', () => socket?.close())
    }

    fetchJson<DashboardData>('/api/dashboard', { signal: controller.signal }, 10_000)
      .then((snapshot) => {
        if (!isDashboardData(snapshot)) {
          throw new ApiError({
            code: 'INVALID_RESPONSE',
            messageKo: '프로그램 서버의 화면 데이터가 올바르지 않습니다.',
          })
        }
        applySnapshot(snapshot)
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
    }
  }, [applySnapshot])

  const updateDashboard = useCallback(async (path: string, init: RequestInit) => {
    const snapshot = await fetchJson<DashboardData>(path, init)
    if (!isDashboardData(snapshot)) {
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        messageKo: '프로그램 서버의 화면 데이터가 올바르지 않습니다.',
      })
    }
    applySnapshot(snapshot)
    return snapshot
  }, [applySnapshot])

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
      return await updateDashboard(`/api/control/${action}`, { method: 'POST' })
    } catch (error) {
      if (mounted.current) setRequestError(errorMessage(error))
      throw error
    }
  }, [submitLongControl, updateDashboard])

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
    (symbol: string, intervalSeconds: number) =>
      updateDashboard('/api/control/chart', {
        method: 'POST',
        body: JSON.stringify({ symbol, interval_seconds: intervalSeconds }),
      }),
    [updateDashboard],
  )

  const configureStrategy = useCallback(
    (
      strategyId: string,
      configuration: { mode: 'ACTIVE' | 'SHADOW' | 'OFF'; long_enabled: boolean; short_enabled: boolean; expected_revision: number },
    ) =>
      updateDashboard(`/api/strategies/${encodeURIComponent(strategyId)}`, {
        method: 'POST',
        body: JSON.stringify({
          ...configuration,
          manual_lock: true,
          reason: 'USER_CONFIGURATION',
        }),
      }),
    [updateDashboard],
  )

  const rollbackStrategy = useCallback(
    (strategyId: string, targetRevision: number, expectedRevision: number) =>
      updateDashboard(`/api/strategies/${encodeURIComponent(strategyId)}/rollback`, {
        method: 'POST',
        body: JSON.stringify({
          target_revision: targetRevision,
          expected_revision: expectedRevision,
          reason: `USER_ROLLBACK_TO_REV_${targetRevision}`,
        }),
      }),
    [updateDashboard],
  )

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
    controlOperation: data.control_operation ?? submittedOperation,
    control,
    cancelControl,
    retryControl,
    selectChart,
    configureStrategy,
    rollbackStrategy,
    clearError,
  }
}
