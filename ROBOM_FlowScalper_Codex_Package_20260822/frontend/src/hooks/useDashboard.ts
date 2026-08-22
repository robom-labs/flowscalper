// 초기 snapshot과 WebSocket 갱신, PAPER 제어 요청을 한 훅으로 캡슐화한다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { initialDashboard } from '../demoData'
import type { DashboardData } from '../types'

type ControlAction =
  | 'pause'
  | 'resume'
  | 'emergency-close'
  | 'new-run'
  | 'start-live'
  | 'start-demo'

export function useDashboard() {
  const [data, setData] = useState<DashboardData>(initialDashboard)
  const [connected, setConnected] = useState(false)
  const [connectionState, setConnectionState] = useState<'CONNECTING' | 'CONNECTED' | 'RECONNECTING'>('CONNECTING')
  const [lastUpdateMs, setLastUpdateMs] = useState<number | null>(null)
  const hasConnected = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    let socket: WebSocket | undefined
    let reconnectTimer: number | undefined
    let disposed = false

    const connect = () => {
      if (!('WebSocket' in window)) return
      setConnectionState(hasConnected.current ? 'RECONNECTING' : 'CONNECTING')
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${window.location.host}/ws`)
      socket.addEventListener('open', () => {
        hasConnected.current = true
        setConnected(true)
        setConnectionState('CONNECTED')
      })
      socket.addEventListener('message', (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as {
            type: string
            data: DashboardData
          }
          if (payload.type === 'dashboard') {
            setData(payload.data)
            setLastUpdateMs(Date.now())
          }
        } catch {
          setConnectionState('RECONNECTING')
        }
      })
      socket.addEventListener('close', () => {
        setConnected(false)
        setConnectionState('RECONNECTING')
        if (!disposed) reconnectTimer = window.setTimeout(connect, 1000)
      })
    }

    fetch('/api/dashboard', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`dashboard ${response.status}`)
        return response.json() as Promise<DashboardData>
      })
      .then((snapshot) => {
        setData(snapshot)
        setLastUpdateMs(Date.now())
      })
      .catch(() => undefined)
    connect()

    return () => {
      disposed = true
      controller.abort()
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  const updateDashboard = useCallback(async (path: string, init: RequestInit) => {
    const response = await fetch(path, init)
    if (!response.ok) throw new Error(`${path} ${response.status}`)
    const snapshot = (await response.json()) as DashboardData
    setData(snapshot)
    setLastUpdateMs(Date.now())
    return snapshot
  }, [])

  const control = useCallback(
    (action: ControlAction) => updateDashboard(`/api/control/${action}`, { method: 'POST' }),
    [updateDashboard],
  )

  const selectChart = useCallback(
    (symbol: string, intervalSeconds: number) =>
      updateDashboard('/api/control/chart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, interval_seconds: intervalSeconds }),
      }),
    [updateDashboard],
  )

  const configureStrategy = useCallback(
    (
      strategyId: string,
      configuration: { mode: 'ACTIVE' | 'SHADOW' | 'OFF'; long_enabled: boolean; short_enabled: boolean },
    ) =>
      updateDashboard(`/api/strategies/${encodeURIComponent(strategyId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configuration),
      }),
    [updateDashboard],
  )

  return {
    data,
    connected,
    connectionState,
    lastUpdateMs,
    control,
    selectChart,
    configureStrategy,
  }
}
