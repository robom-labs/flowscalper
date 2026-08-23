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

test('renders permanent paper-only ready status and beginner home controls', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  expect(screen.getByText('모의매매 · PAPER')).toBeInTheDocument()
  expect(screen.getByText('실제 주문 0')).toBeInTheDocument()
  expect(screen.getAllByText(/시작 준비 완료/).length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '자동 관찰 홈' })).toBeInTheDocument()
  expect(screen.getByText('6개 독립 전략 합계')).toBeInTheDocument()
  expect(screen.getByText(/한 개의 실제 1,000 USDT 계좌 결과가 아닙니다/)).toBeInTheDocument()
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
    ['전략 리그', '전략 리그'],
    ['진행 거래', '진행 거래'],
    ['거래 기록', '거래 기록'],
    ['과거 재생', '과거 데이터 다시 보기'],
    ['성과', '성과'],
    ['안전 설정', '안전 설정'],
    ['고급 터미널', '고급 터미널'],
    ['시스템', '시스템 상태'],
  ]) {
    fireEvent.click(screen.getByRole('button', { name: button }))
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getByText('모의매매 · PAPER')).toBeInTheDocument()
  }
})

test('shows an explicit initial backend failure instead of pretending LIVE', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  const alerts = await screen.findAllByRole('alert')
  expect(alerts.some((alert) => alert.textContent?.includes('프로그램 서버에 연결하지 못했습니다.'))).toBe(true)
  expect(screen.getByText('모의매매 · PAPER')).toBeInTheDocument()
  expect(screen.queryByText('LIVE DATA')).not.toBeInTheDocument()
})
