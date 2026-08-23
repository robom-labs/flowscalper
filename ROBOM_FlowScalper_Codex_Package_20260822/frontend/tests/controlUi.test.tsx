// 비동기 제어 버튼의 중복방지·operation·취소·재시도·timeout·WebSocket 복구를 검증한다.
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import type { ControlOperation, DashboardData } from '../src/types'
import { dashboardFixture } from './fixtures'

class FakeWebSocket extends EventTarget {
  static instances: FakeWebSocket[] = []
  closed = false

  constructor(url: string) {
    super()
    void url
    FakeWebSocket.instances.push(this)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.dispatchEvent(new Event('close'))
  }

  open() {
    this.dispatchEvent(new Event('open'))
  }

  dashboard(data: DashboardData) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'dashboard', data }) }))
  }

  malformed() {
    this.dispatchEvent(new MessageEvent('message', { data: '{broken' }))
  }
}

const operation = (state: ControlOperation['state'], id = 'control-test'): ControlOperation => ({
  operation_id: id,
  action: 'START_LIVE',
  state,
  stage_ko: state === 'FAILED_RETRYABLE' ? '공개시장에 연결하지 못했습니다.' : state === 'PREPARING' ? '공개시장 연결 준비 중' : '요청을 받았습니다',
  started_ts_ms: 1,
  updated_ts_ms: 2,
  finished_ts_ms: state === 'REQUESTED' || state === 'PREPARING' ? null : 3,
  retryable: state === 'FAILED_RETRYABLE',
  error_code: state === 'FAILED_RETRYABLE' ? 'PUBLIC_DATA_UNAVAILABLE' : null,
  error_message_ko: state === 'FAILED_RETRYABLE' ? '공개시장에 연결하지 못했습니다.' : null,
  history: [],
})

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('scrollTo', vi.fn())
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test('posts start-live once, renders 202 operation updates and cancels it', async () => {
  const data = dashboardFixture()
  const requested = operation('REQUESTED')
  const cancelled = { ...requested, state: 'CANCELLED' as const, stage_ko: '연결 작업을 취소했습니다', finished_ts_ms: 3 }
  const fetchMock = vi.fn(async (path: RequestInfo | URL) => {
    const url = String(path)
    if (url === '/api/dashboard') return response(data)
    if (url === '/api/control/start-live') return response(requested, 202)
    if (url.endsWith('/cancel')) return response(cancelled, 202)
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)
  const start = await screen.findByRole('button', { name: '자동 관찰 시작' })
  fireEvent.click(start)
  fireEvent.click(start)
  await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path) === '/api/control/start-live')).toHaveLength(1))
  await waitFor(() => expect(document.querySelector('.operation-card h3')).toHaveTextContent('요청을 받았습니다'))

  FakeWebSocket.instances[0].dashboard({ ...data, control_operation: operation('PREPARING') })
  await waitFor(() => expect(document.querySelector('.operation-card h3')).toHaveTextContent('공개시장 연결 준비 중'))
  fireEvent.click(screen.getByRole('button', { name: '연결 취소' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/cancel'))).toBe(true))
})

test('retries FAILED_RETRYABLE with a new start-live request', async () => {
  const failed = operation('FAILED_RETRYABLE', 'control-failed')
  const data = { ...dashboardFixture(), control_operation: failed }
  const fetchMock = vi.fn(async (path: RequestInfo | URL) => (
    String(path) === '/api/dashboard'
      ? response(data)
      : response(operation('REQUESTED', 'control-retry'), 202)
  ))
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: '다시 시도' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/control/start-live')).toBe(true))
})

test('recovers the button and shows Korean detail after HTTP 500', async () => {
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => (
    String(path) === '/api/dashboard'
      ? response(data)
      : response({ detail: { error_code: 'CONTROL_FAILED', error_message_ko: '서버가 실행 작업을 완료하지 못했습니다.', retryable: true } }, 500)
  )))
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: '자동 관찰 시작' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('서버가 실행 작업을 완료하지 못했습니다.')
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeEnabled()
})

test('aborts a stalled control after 15 seconds and restores the button', async () => {
  vi.useFakeTimers()
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    if (String(path) === '/api/dashboard') return Promise.resolve(response(data))
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
  }))
  render(<App />)
  await act(async () => Promise.resolve())
  const start = screen.getByRole('button', { name: '자동 관찰 시작' })
  fireEvent.click(start)
  expect(start).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001) })
  expect(screen.getByRole('alert')).toHaveTextContent('요청 시간이 초과되었습니다.')
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeEnabled()
})

test('marks malformed WebSocket data as reconnecting and recovers on a valid socket', async () => {
  vi.useFakeTimers()
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn(async () => response(data)))
  render(<App />)
  await act(async () => Promise.resolve())
  expect(FakeWebSocket.instances).toHaveLength(1)
  act(() => FakeWebSocket.instances[0].malformed())
  expect(document.querySelector('.connection-error')).toHaveTextContent('다시 연결')
  await act(async () => { await vi.advanceTimersByTimeAsync(1_001) })
  expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2)
  const recovered = FakeWebSocket.instances.at(-1) as FakeWebSocket
  act(() => { recovered.open(); recovered.dashboard(data) })
  expect(document.querySelector('.connection-error')).toBeNull()
  expect(screen.getByText('화면 연결됨')).toBeInTheDocument()
})
