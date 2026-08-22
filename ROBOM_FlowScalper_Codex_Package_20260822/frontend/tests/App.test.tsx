// 대시보드가 PAPER 안전 문구를 영구 표시하는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { initialDashboard } from '../src/demoData'

class FakeWebSocket extends EventTarget {
  close() {}
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('renders permanent paper-only READY status', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  expect(screen.getByText('PAPER')).toBeInTheDocument()
  expect(screen.getByText('실제 주문 없음')).toBeInTheDocument()
  expect(screen.getAllByText(/READY/).length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: '실시간 PAPER 시작' })).toBeInTheDocument()
  expect(screen.queryByText('LIVE DATA')).not.toBeInTheDocument()
})

test('navigates all workflows while keeping the paper banner', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => initialDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  for (const [button, heading] of [
    ['거래내역', '거래내역'],
    ['리플레이', '결정적 리플레이'],
    ['성과분석', '성과분석'],
    ['위험관리', '위험관리'],
    ['시스템', '시스템'],
  ]) {
    fireEvent.click(screen.getByRole('button', { name: button }))
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getByText('PAPER')).toBeInTheDocument()
  }
})
