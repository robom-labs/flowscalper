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

test('renders permanent paper-only ready status in simple words', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  expect(screen.getByText('모의매매 · PAPER')).toBeInTheDocument()
  expect(screen.getByText('실제 주문 0')).toBeInTheDocument()
  expect(screen.getAllByText(/시작 준비 완료/).length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '5선' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '10선' })).toHaveAttribute('aria-pressed', 'true')
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
    ['매매 설정', '매매 설정'],
    ['거래 기록', '거래 기록'],
    ['과거 재생', '과거 데이터 다시 보기'],
    ['성과', '모의매매 결과'],
    ['안전 설정', '안전 설정'],
    ['시스템', '시스템 상태'],
  ]) {
    fireEvent.click(screen.getByRole('button', { name: button }))
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getByText('모의매매 · PAPER')).toBeInTheDocument()
  }
})
