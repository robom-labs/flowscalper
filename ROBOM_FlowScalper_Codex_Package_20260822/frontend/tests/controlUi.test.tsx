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
  sequence = 0

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
    this.sequence += 1
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({
      schema_version: 1,
      sequence: this.sequence,
      type: 'snapshot',
      data,
    }) }))
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
  idempotency_key: 'ui-test-key',
  revision: 1,
  actor: 'USER_UI',
  reason: 'USER_START_LIVE',
  history: [],
})

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

const flatPaperSafety = {
  paper_only: true,
  real_orders_enabled: false,
  auth_required: false,
  private_api_enabled: false,
  api_key_enabled: false,
  wallet_enabled: false,
  runtime_ai_order_decision_enabled: false,
  funding_readiness: 'NOT_READY',
} as const

const strategySummary = (data: DashboardData) => ({
  schema_version: 1,
  analysis_scope: 'CURRENT_STRATEGY_VERSION',
  strategies: data.strategies,
  league_accounts: data.league_accounts,
  strategy_count: data.strategies.length,
  league_account_count: data.league_accounts.length,
  ...flatPaperSafety,
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
  const fetchMock = vi.fn(async (path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path)
    if (url === '/api/ui/summary') return response(data)
    if (url === '/api/strategies/summary') return response(strategySummary(data))
    if (url === '/api/control/start-live') {
      if (!init) throw new Error('start-live request options are required')
      return response(requested, 202)
    }
    if (url.endsWith('/cancel')) return response(cancelled, 202)
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)
  const start = await screen.findByRole('button', { name: '자동 관찰 시작' })
  act(() => FakeWebSocket.instances[0].open())
  await waitFor(() => expect(start).toBeEnabled())
  fireEvent.click(start)
  fireEvent.click(start)
  await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path) === '/api/control/start-live')).toHaveLength(1))
  const startRequest = fetchMock.mock.calls.find(([path]) => String(path) === '/api/control/start-live')
  expect(startRequest?.[1]?.headers).toMatchObject({ 'Idempotency-Key': expect.any(String) })
  expect(JSON.parse(String(startRequest?.[1]?.body))).toMatchObject({
    expected_revision: 0,
    reason: 'USER_START_LIVE',
  })
  await waitFor(() => expect(screen.getByLabelText('프로그램 작동 상태')).toHaveTextContent('요청을 받았습니다'))

  FakeWebSocket.instances[0].dashboard({ ...data, control_operation: operation('PREPARING') })
  await waitFor(() => expect(screen.getByLabelText('프로그램 작동 상태')).toHaveTextContent('공개시장 연결 준비 중'))
  fireEvent.click(screen.getByRole('button', { name: '연결 취소' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/cancel'))).toBe(true))
})

test('retries FAILED_RETRYABLE with a new start-live request', async () => {
  const failed = operation('FAILED_RETRYABLE', 'control-failed')
  const data = { ...dashboardFixture(), control_operation: failed }
  const fetchMock = vi.fn(async (path: RequestInfo | URL) => (
    String(path) === '/api/ui/summary'
      ? response(data)
      : String(path) === '/api/strategies/summary'
        ? response(strategySummary(data))
      : response(operation('REQUESTED', 'control-retry'), 202)
  ))
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)
  const retry = await screen.findByRole('button', { name: '다시 연결' })
  act(() => FakeWebSocket.instances[0].open())
  expect(screen.queryByRole('button', { name: '자동 관찰 시작' })).not.toBeInTheDocument()
  fireEvent.click(retry)
  await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/control/start-live')).toBe(true))
})

test('recovers the button and shows Korean detail after HTTP 500', async () => {
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => (
    String(path) === '/api/ui/summary'
      ? response(data)
      : String(path) === '/api/strategies/summary'
        ? response(strategySummary(data))
      : response({ detail: { error_code: 'CONTROL_FAILED', error_message_ko: '서버가 실행 작업을 완료하지 못했습니다.', retryable: true } }, 500)
  )))
  render(<App />)
  const start = await screen.findByRole('button', { name: '자동 관찰 시작' })
  act(() => FakeWebSocket.instances[0].open())
  await waitFor(() => expect(start).toBeEnabled())
  fireEvent.click(start)
  expect(await screen.findByRole('alert')).toHaveTextContent('서버가 실행 작업을 완료하지 못했습니다.')
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeEnabled()
})

test('aborts a stalled control after 15 seconds and restores the button', async () => {
  vi.useFakeTimers()
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    if (String(path) === '/api/ui/summary') return Promise.resolve(response(data))
    if (String(path) === '/api/strategies/summary') return Promise.resolve(response(strategySummary(data)))
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
  }))
  render(<App />)
  await act(async () => Promise.resolve())
  const start = screen.getByRole('button', { name: '자동 관찰 시작' })
  act(() => FakeWebSocket.instances[0].open())
  expect(start).toBeEnabled()
  fireEvent.click(start)
  expect(start).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001) })
  expect(screen.getByRole('alert')).toHaveTextContent('요청 시간이 초과되었습니다.')
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeEnabled()
})

test('marks malformed WebSocket data as reconnecting and recovers on a valid socket', async () => {
  vi.useFakeTimers()
  const data = dashboardFixture()
  vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => response(
    String(path) === '/api/strategies/summary'
      ? {
          schema_version: 1,
          analysis_scope: 'CURRENT_STRATEGY_VERSION',
          strategies: data.strategies,
          league_accounts: data.league_accounts,
          strategy_count: data.strategies.length,
          league_account_count: data.league_accounts.length,
          ...flatPaperSafety,
        }
      : data,
  )))
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

test('shows automatic safety waiting without a misleading manual resume button', async () => {
  const base = dashboardFixture()
  const safetyWaiting: DashboardData = {
    ...base,
    paused: true,
    status: {
      ...base.status,
      mode: 'LIVE_SHADOW_PAPER',
      market_data_state: 'LIVE',
      venue: 'BINANCE_USDM',
      processing_lag_p95_ms: 2_140,
      health_flags: ['PUBLIC_SUPERVISOR_RUNNING', 'CRITICAL_MARKET_LAG_ENTRY_LOCK', 'PAPER_ENTRIES_PAUSED'],
    },
    operation_status: {
      state: 'SAFETY_WAITING',
      title_ko: '작동 중 · 안전 대기',
      detail_ko: '시장 관찰은 계속 중입니다. 데이터가 정상화되면 새 PAPER 진입도 자동으로 다시 시작합니다.',
      market_observation_active: true,
      paper_entry_active: false,
      automatic_recovery: true,
      recommended_action: 'NONE',
      lag_p95_ms: 2_140,
    },
  }
  vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => response(
    String(path) === '/api/strategies/summary'
      ? strategySummary(safetyWaiting)
      : safetyWaiting,
  )))

  render(<App />)

  const panel = await screen.findByLabelText('프로그램 작동 상태')
  await waitFor(() => expect(panel).toHaveTextContent('작동 중 · 안전 대기'))
  expect(panel).toHaveTextContent('시장 관찰계속 작동')
  expect(panel).toHaveTextContent('정상화되면 자동으로 다시 시작합니다.')
  expect(screen.queryByRole('button', { name: '새 진입 다시 시작' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '설정' }))
  expect(await screen.findByLabelText('자동 진입 제어 상태')).toHaveTextContent('작동 중 · 안전 대기')
  expect(screen.queryByRole('button', { name: '자동 관찰 계속하기' })).not.toBeInTheDocument()
})

test('keeps an exceptional manual pause visible without exposing a routine stop control', async () => {
  const base = dashboardFixture()
  const manuallyPaused: DashboardData = {
    ...base,
    paused: true,
    status: {
      ...base.status,
      mode: 'LIVE_SHADOW_PAPER',
      market_data_state: 'LIVE',
      venue: 'BINANCE_USDM',
      health_flags: ['PUBLIC_SUPERVISOR_RUNNING', 'PAPER_ENTRIES_PAUSED'],
    },
    operation_status: {
      state: 'MANUALLY_PAUSED',
      title_ko: '사용자가 일시정지',
      detail_ko: '시장 관찰은 계속 중이며 다음 정상 재시작에서 자동 진입이 복구됩니다.',
      market_observation_active: true,
      paper_entry_active: false,
      automatic_recovery: false,
      recommended_action: 'NONE',
      lag_p95_ms: 110,
    },
    paper_entry_intent: {
      state: 'USER_PAUSED',
      manual_pause_requested: true,
      revision: 7,
      actor: 'USER_UI',
      reason: 'USER_PAUSE',
      updated_ts_ms: 1,
      reversible: true,
    },
  }
  const fetchMock = vi.fn(async (path: RequestInfo | URL) => {
    if (String(path) === '/api/strategies/summary') {
      return response(strategySummary(manuallyPaused))
    }
    return response(manuallyPaused)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  const panel = await screen.findByLabelText('프로그램 작동 상태')
  expect(panel).toHaveTextContent('사용자가 일시정지')
  expect(screen.getByText('자동 관찰 · 시작됨')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '새 진입 다시 시작' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '새 진입 잠시 멈춤' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/control/'))).toBe(false)
})
