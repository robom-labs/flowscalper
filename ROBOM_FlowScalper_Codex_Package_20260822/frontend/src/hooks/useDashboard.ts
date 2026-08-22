// 초기 snapshot과 WebSocket 갱신, PAPER 제어 요청을 한 훅으로 캡슐화한다.
import { useCallback, useEffect, useState } from 'react'
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

  useEffect(() => {
    const controller = new AbortController()
    let socket: WebSocket | undefined
    let reconnectTimer: number | undefined
    let disposed = false

    const connect = () => {
      if (!('WebSocket' in window)) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${window.location.host}/ws`)
      socket.addEventListener('open', () => setConnected(true))
      socket.addEventListener('message', (event) => {
        const payload = JSON.parse(String(event.data)) as {
          type: string
          data: DashboardData
        }
        if (payload.type === 'dashboard') setData(payload.data)
      })
      socket.addEventListener('close', () => {
        setConnected(false)
        if (!disposed) reconnectTimer = window.setTimeout(connect, 1000)
      })
    }

    fetch('/api/dashboard', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`dashboard ${response.status}`)
        return response.json() as Promise<DashboardData>
      })
      .then(setData)
      .catch(() => undefined)
    connect()

    return () => {
      disposed = true
      controller.abort()
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  const control = useCallback(async (action: ControlAction) => {
    const response = await fetch(`/api/control/${action}`, { method: 'POST' })
    if (!response.ok) throw new Error(`control ${response.status}`)
    const snapshot = (await response.json()) as DashboardData
    setData(snapshot)
  }, [])

  return { data, connected, control }
}
