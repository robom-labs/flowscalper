// 대시보드가 PAPER 안전 문구를 영구 표시하는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { initialDashboard } from '../src/demoData'

class FakeWebSocket extends EventTarget {
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('scrollTo', vi.fn())
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('renders permanent paper-only ready status and market controls', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  expect(screen.getByText('PAPER · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getByText(/시작 준비 완료/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '공개시장 시작' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '샘플로 보기' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'BTCUSDT 시장' })).toBeInTheDocument()
  expect(screen.queryByText('LIVE DATA')).not.toBeInTheDocument()
})

test('navigates five main groups and contextual subpages', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => initialDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  for (const [button, heading] of [
    ['전략', '전략 설정'],
    ['기록', '거래 기록'],
    ['분석', '성과'],
    ['설정', '시스템 상태'],
    ['시장', 'BTCUSDT 시장'],
  ]) {
    fireEvent.click(screen.getByRole('button', { name: button }))
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getByText('PAPER · 실제 주문 0')).toBeInTheDocument()
  }
})

test('shows an explicit initial backend failure instead of pretending LIVE', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  const alerts = await screen.findAllByRole('alert')
  expect(alerts.some((alert) => alert.textContent?.includes('프로그램 서버에 연결하지 못했습니다.'))).toBe(true)
  expect(screen.getByText('PAPER · 실제 주문 0')).toBeInTheDocument()
  expect(screen.queryByText('LIVE DATA')).not.toBeInTheDocument()
})

test('keeps demo truth visible in both the permanent header and market workspace', async () => {
  const demoDashboard = {
    ...initialDashboard,
    status: {
      ...initialDashboard.status,
      mode: 'DEMO_FIXTURE' as const,
      market_data_state: 'FIXTURE' as const,
      venue: 'FIXTURE',
      processing_lag_p95_ms: null,
      health_flags: ['OFFLINE_DEMO_ISOLATED'],
    },
    chart: { ...initialDashboard.chart, fixture: true },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => demoDashboard })),
  )
  class DemoWebSocket extends EventTarget {
    close() {}
    constructor() {
      super()
      queueMicrotask(() => {
        this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'dashboard', data: demoDashboard }) }))
      })
    }
  }
  vi.stubGlobal('WebSocket', DemoWebSocket)

  render(<App />)

  expect(await screen.findByText('샘플 PAPER 데이터 · LIVE 아님 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getByText('샘플 PAPER · LIVE 아님 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '샘플 재생 멈춤' })).toBeInTheDocument()
})

test('renders an explicit compact LIVE PAPER observation banner', async () => {
  const liveDashboard = {
    ...initialDashboard,
    status: {
      ...initialDashboard.status,
      mode: 'LIVE_SHADOW_PAPER' as const,
      market_data_state: 'LIVE' as const,
      venue: 'BINANCE_USDM',
      health_flags: ['PUBLIC_SUPERVISOR_RUNNING', 'NO_AUTH_HEADERS'],
    },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => liveDashboard })),
  )
  class LiveWebSocket extends EventTarget {
    close() {}
    constructor() {
      super()
      queueMicrotask(() => {
        this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'dashboard', data: liveDashboard }) }))
      })
    }
  }
  vi.stubGlobal('WebSocket', LiveWebSocket)

  render(<App />)

  expect(await screen.findByText('공개시장 자동 관찰 중 · PAPER · 실제 주문 0')).toBeInTheDocument()
})
