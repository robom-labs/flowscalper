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
