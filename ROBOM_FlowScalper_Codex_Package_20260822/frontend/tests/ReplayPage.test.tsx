// 과거 재생 화면이 빠른 미리보기를 먼저 열고 정밀 이벤트는 사용자 요청 때만 읽는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayPage } from '../src/pages/ReplayPage'

vi.mock('../src/components/PriceChart', () => ({
  PriceChart: () => <div data-testid="replay-chart" />,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('loads recent candles first and defers the expensive event timeline until requested', async () => {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    requests.push(url)
    if (url === '/api/replay/runs') {
      return new Response(JSON.stringify([{
        run_id: 'run-preview', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
        started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 10_000,
        events_saved: true, trade_count: 0, shadow_trade_count: 2,
      }]), { status: 200 })
    }
    if (url === '/api/replay/results') return new Response('[]', { status: 200 })
    if (url === '/api/replay/operations/current') return new Response('null', { status: 200 })
    if (url.includes('/preview?')) {
      return new Response(JSON.stringify({
        run_id: 'run-preview', symbol: 'BTCUSDT', total_events: 10_000,
        truncated: true, available_symbols: [{ symbol: 'BTCUSDT', event_count: 10_000 }],
        events: [], candles: [{
          time: 1, open_ts_ms: 1_000, open: 100, high: 101, low: 99,
          close: 100.5, volume: 2, trade_count: 3,
        }], preview_only: true,
      }), { status: 200 })
    }
    if (url.includes('/timeline?')) {
      return new Response(JSON.stringify({
        run_id: 'run-preview', symbol: 'BTCUSDT', total_events: 10_000,
        truncated: true, available_symbols: [{ symbol: 'BTCUSDT', event_count: 10_000 }],
        events: [{ event_id: 'event-1', symbol: 'BTCUSDT', event_type: 'BOOK', venue_ts_ms: 1_000, data: { bid: 100, ask: 101 } }],
        candles: [], preview_only: false,
      }), { status: 200 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayPage />)

  expect(await screen.findByText(/빠른 미리보기 · BTCUSDT 최근 캔들 1개/)).toBeInTheDocument()
  expect(requests.some((url) => url.includes('/timeline?'))).toBe(false)
  expect(screen.getByRole('button', { name: '같은 조건으로 전략 검증' })).toBeDisabled()

  fireEvent.click(screen.getByRole('button', { name: '정밀 이벤트 불러오기' }))

  await waitFor(() => expect(requests.some((url) => url.includes('/timeline?'))).toBe(true))
  expect(requests).toContain('/api/replay/run-preview/timeline?symbol=BTCUSDT&limit=100')
  expect(await screen.findByText(/정밀 이벤트 1개를 불러왔습니다/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '같은 조건으로 전략 검증' })).toBeEnabled()
})

test('restores an active strategy verification and exposes a real cancel control', async () => {
  const operation = {
    operation_id: 'replay-operation-active', source_run_id: 'run-active', symbol: 'ETHUSDT',
    total_events: 12_345, state: 'PROCESSING', stage_ko: '같은 전략 조건으로 검증하고 있습니다',
    started_ts_ms: Date.now() - 5_000, updated_ts_ms: Date.now(), finished_ts_ms: null,
    retryable: false, error_code: null, error_message_ko: null, result: null, revision: 3,
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/replay/runs') return new Response(JSON.stringify([{
      run_id: 'run-active', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
      started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 12_345,
      events_saved: true, trade_count: 1, shadow_trade_count: 3,
    }]), { status: 200 })
    if (url === '/api/replay/results') return new Response('[]', { status: 200 })
    if (url === '/api/replay/operations/current') return new Response(JSON.stringify(operation), { status: 200 })
    if (url.includes('/preview?')) return new Response(JSON.stringify({
      run_id: 'run-active', symbol: 'ETHUSDT', total_events: 12_345, truncated: true,
      available_symbols: [{ symbol: 'ETHUSDT', event_count: 12_345 }], events: [], candles: [], preview_only: true,
    }), { status: 200 })
    if (url === '/api/replay/operations/replay-operation-active' && init?.method === 'DELETE') {
      return new Response(JSON.stringify({ ...operation, state: 'CANCELLING', stage_ko: '저장 Run 검증을 안전하게 취소하고 있습니다', revision: 4 }), { status: 202 })
    }
    if (url === '/api/replay/operations/replay-operation-active') return new Response(JSON.stringify(operation), { status: 200 })
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayPage />)

  expect(await screen.findByText('같은 전략 조건으로 검증하고 있습니다')).toBeInTheDocument()
  expect(screen.getByText(/약 12,345건/)).toBeInTheDocument()
  expect(screen.getByText(/실제 주문과 인증 경로는 0/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '전략 검증 취소' }))
  expect(await screen.findByRole('button', { name: '취소 중' })).toBeDisabled()
})
