// 과거 재생 화면이 빠른 미리보기를 먼저 열고 정밀 이벤트는 사용자 요청 때만 읽는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayViewer } from '../src/components/ReplayViewer'
import type { HistoryRow } from '../src/types'

vi.mock('../src/components/PriceChart', () => ({
  PriceChart: ({ chart }: { chart: { candles: unknown[] } }) => <div data-testid="replay-chart" data-candle-count={chart.candles.length} />,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('loads recent candles first and defers the expensive event timeline until requested', async () => {
  const requests: string[] = []
  let replayRequest: RequestInit | undefined
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    if (url === '/api/replay/run-preview' && init?.method === 'POST') {
      replayRequest = init
      return new Response(JSON.stringify({
        operation_id: 'replay-operation-fixed-scope', source_run_id: 'run-preview',
        symbol: 'BTCUSDT', total_events: 10_000, state: 'CANCELLED',
        stage_ko: '검증 범위 고정 확인', started_ts_ms: 2_000, updated_ts_ms: 2_000,
        finished_ts_ms: 2_000, retryable: false, error_code: null,
        error_message_ko: null, result: null, revision: 1, paper_only: true,
        real_orders_enabled: false, auth_required: false,
      }), { status: 202 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  expect(await screen.findByText(/빠른 미리보기 · BTCUSDT 최근 캔들 1개/)).toBeInTheDocument()
  expect(requests.some((url) => url.includes('/timeline?'))).toBe(false)
  expect(screen.getByRole('button', { name: '같은 조건으로 전략 검증' })).toBeDisabled()

  fireEvent.click(screen.getByRole('button', { name: '정밀 이벤트 불러오기' }))

  await waitFor(() => expect(requests.some((url) => url.includes('/timeline?'))).toBe(true))
  expect(requests).toContain('/api/replay/run-preview/timeline?symbol=BTCUSDT&limit=100')
  expect(await screen.findByText(/정밀 이벤트 1개를 불러왔습니다/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '같은 조건으로 전략 검증' })).toBeEnabled()

  fireEvent.click(screen.getByRole('button', { name: '같은 조건으로 전략 검증' }))

  await waitFor(() => expect(replayRequest).toBeDefined())
  expect(JSON.parse(String(replayRequest?.body))).toEqual({
    symbol: 'BTCUSDT',
    event_limit: 10_000,
  })
})

test('shows the run selector while the first candle preview is still loading', async () => {
  let resolvePreview: ((response: Response) => void) | undefined
  const pendingPreview = new Promise<Response>((resolve) => {
    resolvePreview = resolve
  })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/replay/runs') {
      return new Response(JSON.stringify([{
        run_id: 'run-slow-preview', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
        started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 25_000_000,
        events_saved: true, trade_count: 0, shadow_trade_count: 0,
      }]), { status: 200 })
    }
    if (url === '/api/replay/results') return new Response('[]', { status: 200 })
    if (url === '/api/replay/operations/current') return new Response('null', { status: 200 })
    if (url.includes('/preview?')) return pendingPreview
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  const runSelector = await screen.findByRole('combobox', { name: '저장 기록' })
  await waitFor(() => expect(runSelector).toHaveValue('run-slow-preview'))
  expect(runSelector).toBeEnabled()
  expect(screen.queryByText('저장 기록 목록을 확인하는 중입니다')).not.toBeInTheDocument()
  expect(screen.getByText('저장된 최근 캔들을 불러오는 중입니다.')).toBeInTheDocument()

  resolvePreview?.(new Response(JSON.stringify({
    run_id: 'run-slow-preview', symbol: 'BTCUSDT', total_events: 25_000_000,
    truncated: true, available_symbols: [{ symbol: 'BTCUSDT', event_count: 25_000_000 }],
    events: [], candles: [], preview_only: true,
  }), { status: 200 }))

  expect(await screen.findByText(/빠른 미리보기 · BTCUSDT 최근 캔들 0개/)).toBeInTheDocument()
})

test('aborts an obsolete preview when the user selects another saved run', async () => {
  const pendingFirstPreview = new Promise<Response>(() => undefined)
  let firstPreviewSignal: AbortSignal | undefined
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/replay/runs') {
      return new Response(JSON.stringify([
        {
          run_id: 'run-first', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
          started_ts_ms: 2_000, finalized_ts_ms: null, market_event_count: 100,
          events_saved: true, trade_count: 0, shadow_trade_count: 0,
        },
        {
          run_id: 'run-second', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
          started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 200,
          events_saved: true, trade_count: 0, shadow_trade_count: 0,
        },
      ]), { status: 200 })
    }
    if (url === '/api/replay/results') return new Response('[]', { status: 200 })
    if (url === '/api/replay/operations/current') return new Response('null', { status: 200 })
    if (url.includes('/api/replay/run-first/preview?')) {
      firstPreviewSignal = init?.signal ?? undefined
      return pendingFirstPreview
    }
    if (url.includes('/api/replay/run-second/preview?')) {
      return new Response(JSON.stringify({
        run_id: 'run-second', symbol: 'ETHUSDT', total_events: 200,
        truncated: true, available_symbols: [{ symbol: 'ETHUSDT', event_count: 200 }],
        events: [], candles: [], preview_only: true,
      }), { status: 200 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  const runSelector = await screen.findByRole('combobox', { name: '저장 기록' })
  await waitFor(() => expect(firstPreviewSignal).toBeDefined())
  fireEvent.change(runSelector, { target: { value: 'run-second' } })

  await waitFor(() => expect(firstPreviewSignal?.aborted).toBe(true))
  expect(await screen.findByText(/빠른 미리보기 · ETHUSDT 최근 캔들 0개/)).toBeInTheDocument()
})

test('offers one clear retry action when the saved-run preview fails', async () => {
  let previewAttempts = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/replay/runs') return new Response(JSON.stringify([{
      run_id: 'run-retry', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
      started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 300,
      events_saved: true, trade_count: 0, shadow_trade_count: 0,
    }]), { status: 200 })
    if (url === '/api/replay/results') return new Response('[]', { status: 200 })
    if (url === '/api/replay/operations/current') return new Response('null', { status: 200 })
    if (url.includes('/preview?')) {
      previewAttempts += 1
      if (previewAttempts === 1) {
        return new Response(JSON.stringify({
          detail: { error_message_ko: '저장 화면 준비가 지연됐습니다. 다시 시도해 주세요.' },
        }), { status: 503 })
      }
      return new Response(JSON.stringify({
        run_id: 'run-retry', symbol: 'SOLUSDT', total_events: 300,
        truncated: true, available_symbols: [{ symbol: 'SOLUSDT', event_count: 300 }],
        events: [], candles: [], preview_only: true,
      }), { status: 200 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  expect(await screen.findByRole('alert')).toHaveTextContent('저장 화면 준비가 지연됐습니다.')
  fireEvent.click(screen.getByRole('button', { name: '미리보기 다시 시도' }))

  expect(await screen.findByText(/빠른 미리보기 · SOLUSDT 최근 캔들 0개/)).toBeInTheDocument()
  expect(previewAttempts).toBe(2)
})

test('restores an active strategy verification and exposes a real cancel control', async () => {
  const operation = {
    operation_id: 'replay-operation-active', source_run_id: 'run-active', symbol: 'ETHUSDT',
    total_events: 12_345, state: 'PROCESSING', stage_ko: '같은 전략 조건으로 검증하고 있습니다',
    started_ts_ms: Date.now() - 5_000, updated_ts_ms: Date.now(), finished_ts_ms: null,
    retryable: false, error_code: null, error_message_ko: null, result: null, revision: 3,
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
  let currentOperation = operation
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
      currentOperation = { ...operation, state: 'CANCELLING', stage_ko: '저장 Run 검증을 안전하게 취소하고 있습니다', revision: 4 }
      return new Response(JSON.stringify(currentOperation), { status: 202 })
    }
    if (url === '/api/replay/operations/replay-operation-active') return new Response(JSON.stringify(currentOperation), { status: 200 })
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  expect(await screen.findByText('같은 전략 조건으로 검증하고 있습니다')).toBeInTheDocument()
  expect(screen.getByText(/고정 입력 12,345건/)).toBeInTheDocument()
  expect(screen.getByText('공개시장 PAPER 관찰은 계속됩니다.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '전략 검증 취소' }))
  expect(await screen.findByRole('button', { name: '취소 중' })).toBeDisabled()
})

test('shows a visible retry action when a focused trade chart request fails', async () => {
  let attempts = 0
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    requests.push(url)
    if (url === '/api/replay/runs' || url === '/api/replay/results') {
      return new Response('[]', { status: 200 })
    }
    if (url === '/api/replay/operations/current') {
      return new Response('null', { status: 200 })
    }
    if (url.includes('/focus?')) {
      attempts += 1
      return new Response(JSON.stringify({ detail: { error_message_ko: '원장 캐시가 사용 중입니다.' } }), { status: 500 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))
  const trade = {
    run_id: 'run-focus-error', trade_id: 'trade-focus-error', profile: 'STRESS',
    symbol: 'XRPUSDT',
  } as unknown as HistoryRow

  render(<ReplayViewer trade={trade} />)

  expect(await screen.findByRole('heading', { name: 'XRPUSDT 거래 차트를 열지 못했습니다' })).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('원장 캐시가 사용 중입니다.')
  fireEvent.click(screen.getByRole('button', { name: '거래 차트 다시 시도' }))
  await waitFor(() => expect(attempts).toBe(2))
  expect(requests).toHaveLength(2)
  expect(requests.every((url) => url.includes('/focus?'))).toBe(true)
})

test('shows allocated entry and exit fees at the matching replay stage', async () => {
  let focusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/replay/runs' || url === '/api/replay/results') {
      return new Response('[]', { status: 200 })
    }
    if (url === '/api/replay/operations/current') {
      return new Response('null', { status: 200 })
    }
    if (url.includes('/focus?')) {
      focusRequests += 1
      return new Response(JSON.stringify({
        session_version: 9,
        run_id: 'run-costs', trade_id: 'trade-costs', profile: 'BASE',
        symbol: 'BTCUSDT', side: 'LONG', strategy_id: 'LSA_REVERSAL_V1',
        entry_context: {
          signal_ts_ms: 1_000,
          reason_codes: ['FLOW_CONFIRMED', 'STRUCTURE_CONFIRMED'],
          reason_labels_ko: ['체결과 호가 흐름이 진입 방향을 확인했습니다.', '가격 구조가 진입 방향을 확인했습니다.'],
          regime: 'TREND_UP', regime_ko: '상승 추세', strategy_display_name_ko: '유동성 고갈 반전',
          strategy_summary_ko: '호가와 체결 흐름이 함께 확인된 반전만 연구합니다.',
          entry_hypothesis_ko: '가격 구조와 실제 호가 비용이 모두 맞을 때만 PAPER 진입합니다.',
          required_timeframes: ['3m'], entry_rules_ko: ['가격 구조 확인'], trade_strategy_version: 'V1',
          stop_rationale_ko: '완성 15분봉 눌림 저점 바깥의 보호선입니다.',
          take_profit_1_rationale_ko: '완성 15분봉의 첫 확정 피벗입니다.',
          take_profit_2_rationale_ko: '완성 1시간봉의 다음 확정 피벗입니다.',
          protection_timeframes_ko: ['완성 15분봉', '완성 1시간봉'],
          runner_management_ko: '1차 익절 뒤 남은 수량은 완성봉 ATR 추적선으로 관리합니다.',
          registry_strategy_version: 'V1', registry_metadata_matches_trade: true,
          evidence_ko: '저장된 공개시장 신호·PAPER 원장 기준', paper_only: true,
        },
        levels: { signal_ts_ms: 1_000, entry: '100', initial_stop: '98', take_profit_1: '102', take_profit_2: '104' },
        milestones: [
          { kind: 'SIGNAL', ts_ms: 1_000, price: '100' },
          { kind: 'ENTRY', ts_ms: 2_000, price: '100' },
          { kind: 'EXIT', ts_ms: 3_000, price: '101' },
        ],
        start_ts_ms: 1_000, entry_ts_ms: 2_000, exit_ts_ms: 3_000, end_ts_ms: 3_000,
        default_speed: 5, speeds: [1, 5],
        frames: [
          { ts_ms: 1_000, event_id: 'signal', event_type: 'BOOK', data: { mid: '100' }, phase: 'PRE_ENTRY', markers: [{ kind: 'SIGNAL', ts_ms: 1_000, price: '100' }], fills: [] },
          { ts_ms: 2_000, event_id: 'entry', event_type: 'PAPER_LEDGER_TRANSITION', data: { mid: '100' }, phase: 'OPEN', markers: [{ kind: 'SIGNAL', ts_ms: 1_000, price: '100' }, { kind: 'ENTRY', ts_ms: 2_000, price: '100' }], fills: [] },
          { ts_ms: 3_000, event_id: 'exit', event_type: 'PAPER_LEDGER_TRANSITION', data: { mid: '101' }, phase: 'CLOSED', markers: [{ kind: 'SIGNAL', ts_ms: 1_000, price: '100' }, { kind: 'ENTRY', ts_ms: 2_000, price: '100' }, { kind: 'EXIT', ts_ms: 3_000, price: '101' }], fills: [] },
        ],
        candles: [
          { time: 0, open_ts_ms: 0, open: 99, high: 100, low: 98, close: 100, volume: 2, trade_count: 3, interval_seconds: 1 },
          { time: 2, open_ts_ms: 2_000, open: 100, high: 101, low: 100, close: 101, volume: 4, trade_count: 5, interval_seconds: 1 },
        ], keyframes: [{ frame_index: 0, ts_ms: 1_000 }, { frame_index: 1, ts_ms: 2_000 }, { frame_index: 2, ts_ms: 3_000 }],
        trade: {},
        fills: [
          { fill_id: 'entry', trade_id: 'trade-costs', intent: 'ENTRY', price: '100', quantity: '1', fee_usdt: '0.3', slippage_usdt: '0.02', ts_ms: 2_000 },
          { fill_id: 'exit', trade_id: 'trade-costs', intent: 'EXIT', price: '101', quantity: '1', fee_usdt: '0.2', slippage_usdt: '0.08', ts_ms: 3_000 },
        ],
        profile_comparison: [], reconciliation: { applicable: false, sample_type: 'OFFLINE_FIXTURE', matched: null, reason: 'OFFLINE_FIXTURE_UI_ONLY', replay_checksum: '', replay_final_state: 'NOT_RUN' },
        checksum: 'a'.repeat(64), paper_only: true, real_orders_enabled: false, auth_required: false,
      }), { status: 200 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))
  const trade = {
    run_id: 'run-costs', trade_id: 'trade-costs', profile: 'BASE', symbol: 'BTCUSDT',
    strategy: 'LSA_REVERSAL_V1', side: 'LONG', entry: '100', exit: '101', quantity: '1',
    entry_ts_ms: 2_000, exit_ts_ms: 3_000, initial_stop: '98', take_profit: '104',
    exit_reason: 'EDGE_DECAY', gross_pnl: '1', fees: '0.5', slippage: '0.1',
    net_pnl: '0.4', holding_ms: 1_000, holding_seconds: 1, sample_type: 'LIVE_PUBLIC',
  } as unknown as HistoryRow

  const view = render(<ReplayViewer trade={trade} />)
  expect(await screen.findByRole('heading', { name: 'BTCUSDT 거래 집중 재생' })).toBeInTheDocument()
  expect(screen.getByText('실제 진입 1초 전')).toBeInTheDocument()
  expect(screen.getByText('진입 전 확인').closest('li')).toHaveAttribute('aria-current', 'step')
  expect(screen.getByText('왜 진입했나요?')).toBeInTheDocument()
  expect(screen.getByText('왜 이 익절·손절가인가요?')).toBeInTheDocument()
  expect(screen.getByText('체결과 호가 흐름이 진입 방향을 확인했습니다.')).toBeInTheDocument()
  expect(screen.getByText('완성 15분봉 눌림 저점 바깥의 보호선입니다.')).toBeInTheDocument()
  expect(screen.getByText(/1차 익절 뒤 남은 수량은 완성봉 ATR/)).toBeInTheDocument()
  expect(screen.getByText('3분봉')).toBeInTheDocument()
  expect(screen.getByTestId('replay-chart')).toHaveAttribute('data-candle-count', '1')
  fireEvent.click(screen.getByRole('button', { name: '실제 진입' }))
  view.rerender(<ReplayViewer trade={{ ...trade }} />)
  await waitFor(() => {
    expect(screen.getAllByText('PAPER 보유 중')).toHaveLength(2)
    expect(screen.getByText('진입 후 0초')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: '재생 진행 단계' }).querySelectorAll('li')[1]).toHaveAttribute('aria-current', 'step')
    expect(focusRequests).toBe(1)
  })
  fireEvent.click(screen.getByText('세부 원장·비용·검증 정보'))
  await waitFor(() => {
    expect(screen.getByText('진입 수수료').parentElement).toHaveTextContent('0.3 USDT')
    expect(screen.getByText('종료 수수료').parentElement).toHaveTextContent('0.00 USDT')
    expect(screen.getByText('예상 종료비').parentElement).toHaveTextContent('0.2 USDT')
  })

  fireEvent.click(screen.getByRole('button', { name: '실제 종료' }))
  await waitFor(() => {
    expect(screen.getByTestId('replay-chart')).toHaveAttribute('data-candle-count', '2')
    expect(screen.getByText('1초 보유 후 가격·근거 동시 악화')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: '재생 진행 단계' }).querySelectorAll('li')[2]).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText('진입 수수료').parentElement).toHaveTextContent('0.3 USDT')
    expect(screen.getByText('종료 수수료').parentElement).toHaveTextContent('0.2 USDT')
    expect(screen.getByText('예상 종료비').parentElement).toHaveTextContent('0.00 USDT')
  })
  expect(screen.getByText('어떻게 끝났나요?')).toBeInTheDocument()
  expect(screen.getByText(/가격이 왕복 비용 구간보다 불리하게 움직이고/)).toBeInTheDocument()
  expect(screen.queryByText('TRADE REPLAY')).not.toBeInTheDocument()
  expect(screen.queryByText('BASE')).not.toBeInTheDocument()
})

test('shows a replay result only for the selected run and symbol scope', async () => {
  const checksum = 'a'.repeat(64)
  const inputChecksum = 'b'.repeat(64)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/replay/runs') return new Response(JSON.stringify([{
      run_id: 'run-scope', mode: 'LIVE_SHADOW_PAPER', venue: 'BINANCE_USDM',
      started_ts_ms: 1_000, finalized_ts_ms: null, market_event_count: 200,
      events_saved: true, trade_count: 0, shadow_trade_count: 0,
    }]), { status: 200 })
    if (url === '/api/replay/results') return new Response(JSON.stringify([{
      replay_id: 'replay-btc', source_run_id: 'run-scope', scope_symbol: 'BTCUSDT',
      created_ts_ms: 2_000, checksum, input_checksum: inputChecksum, event_count: 100,
      first_ts_ms: 1_000, last_ts_ms: 2_000, event_type_counts: { TRADE: 100 },
      symbol_counts: { BTCUSDT: 100 }, strategy_evaluation_count: 10,
      qualified_signal_count: 0, candidate_plan_count: 0, main_trade_count: 0,
      shadow_trade_count: 0, decision_path: [], final_state: 'OBSERVING_NO_MAIN_TRADE',
      real_orders_enabled: false, auth_required: false,
    }]), { status: 200 })
    if (url === '/api/replay/operations/current') return new Response('null', { status: 200 })
    if (url.includes('/preview?')) {
      const symbol = url.includes('symbol=ETHUSDT') ? 'ETHUSDT' : 'BTCUSDT'
      return new Response(JSON.stringify({
        run_id: 'run-scope', symbol, total_events: 100, truncated: true,
        available_symbols: [
          { symbol: 'BTCUSDT', event_count: 100 },
          { symbol: 'ETHUSDT', event_count: 100 },
        ], events: [], candles: [], preview_only: true,
      }), { status: 200 })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<ReplayViewer />)

  expect(await screen.findByText('검증 완료 · BTCUSDT')).toBeInTheDocument()
  expect(screen.getByText(checksum)).not.toBeVisible()
  expect(screen.getByText(inputChecksum)).not.toBeVisible()
  fireEvent.click(screen.getByText('고급 검증 정보 보기'))
  expect(screen.getByText(checksum)).toBeVisible()
  expect(screen.getByText(inputChecksum)).toBeVisible()

  fireEvent.change(screen.getByRole('combobox', { name: '종목' }), {
    target: { value: 'ETHUSDT' },
  })

  await waitFor(() => expect(screen.getByRole('combobox', { name: '종목' })).toHaveValue('ETHUSDT'))
  expect(screen.getByText('저장 데이터 확인됨')).toBeInTheDocument()
  expect(screen.queryByText(checksum)).not.toBeInTheDocument()
  expect(screen.queryByText(inputChecksum)).not.toBeInTheDocument()
  expect(screen.getByText('전략 검증 전')).toBeInTheDocument()
})
