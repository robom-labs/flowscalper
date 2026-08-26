// 대시보드가 PAPER 안전 문구를 영구 표시하는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { initialDashboard } from '../src/demoData'
import { MarketPage } from '../src/pages/MarketPage'
import type { FocusPosition } from '../src/types'

class FakeWebSocket extends EventTarget {
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('scrollTo', vi.fn())
})

afterEach(() => {
  cleanup()
  document.querySelectorAll('meta[name="robom-release-commit"]').forEach((element) => element.remove())
  vi.unstubAllGlobals()
})

test('fails closed when immutable frontend and backend release commits differ', async () => {
  const frontendCommit = '1'.repeat(40)
  const backendCommit = '2'.repeat(40)
  const releaseMeta = document.createElement('meta')
  releaseMeta.name = 'robom-release-commit'
  releaseMeta.content = frontendCommit
  document.head.append(releaseMeta)
  const mismatchedDashboard = {
    ...initialDashboard,
    system: { ...initialDashboard.system, release_commit: backendCommit },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => mismatchedDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)

  render(<App />)

  expect(await screen.findByRole('alert')).toHaveTextContent('프로그램 버전이 서로 맞지 않습니다.')
  expect(screen.getByText('111111111111')).toBeInTheDocument()
  expect(screen.getByText('222222222222')).toBeInTheDocument()
  expect(screen.getByText(/실제 주문은 계속 0/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '전략' })).not.toBeInTheDocument()
})

test('renders permanent paper-only ready status and market controls', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  render(<App />)
  expect(screen.getByText('PAPER · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getByText(/시작 준비 완료/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '자동 관찰 시작' })).toBeInTheDocument()
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

test('separates current RSS from peak RSS in advanced diagnostics', async () => {
  const resourceDashboard = {
    ...initialDashboard,
    system: {
      ...initialDashboard.system,
      process_memory_mb: 245.5,
      process_memory_source: 'CURRENT_RSS_LIBPROC',
      process_memory_peak_mb: 323.25,
      process_memory_peak_source: 'PEAK_MAX_RSS',
    },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => resourceDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)

  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: '설정' }))
  fireEvent.click(screen.getByText('고급 진단 보기'))

  expect(await screen.findByText('현재 프로세스 메모리 RSS MB')).toBeInTheDocument()
  expect(screen.getByText('프로세스 최고 메모리 RSS MB')).toBeInTheDocument()
  expect(screen.getByText('245.5')).toBeInTheDocument()
  expect(screen.getByText('323.25')).toBeInTheDocument()
})

test('shows the last startup recovery result in beginner and advanced views', async () => {
  const recoveredDashboard = {
    ...initialDashboard,
    system: {
      ...initialDashboard.system,
      startup_recovery_transition_id: 'recovery-run-fixture-001',
      startup_recovery_previous_state: 'SCANNING',
      startup_recovery_state: 'RECOVERY_REVALIDATION_LOCKED',
      startup_recovery_cause_code: 'PAPER_STATE_RECOVERED',
      startup_recovery_actor: 'RECOVERY',
      startup_recovery_run_id: 'run-fixture',
      startup_recovery_occurred_ts_ms: 1_759_888_000_000,
      startup_recovery_reversible: true,
    },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => recoveredDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)

  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: '설정' }))

  expect(await screen.findByText('마지막 시작 복구')).toBeInTheDocument()
  expect(screen.getByText('상태 복구됨')).toBeInTheDocument()
  expect(screen.getByText('새 공개호가 확인 전까지 자동 잠금')).toBeInTheDocument()
  fireEvent.click(screen.getByText('고급 진단 보기'))
  expect(screen.getByText('시작 복구 결과')).toBeInTheDocument()
  expect(screen.getByText('RECOVERY_REVALIDATION_LOCKED')).toBeInTheDocument()
  expect(screen.getByText('시작 복구 원인 코드')).toBeInTheDocument()
  expect(screen.getByText('PAPER_STATE_RECOVERED')).toBeInTheDocument()
})

test('shows the latest paper lifecycle transition without calling a fill a real order', async () => {
  const transitionedDashboard = {
    ...initialDashboard,
    system: {
      ...initialDashboard.system,
      last_paper_transition_id: 'paper-execution-001',
      last_paper_transition_previous_state: 'ENTRY_PENDING',
      last_paper_transition_state: 'PROTECTED',
      last_paper_transition_cause_code: 'ENTRY_FILLED',
      last_paper_transition_actor: 'AUTO_SAFETY',
      last_paper_transition_account_id: 'MAIN:BASE',
      last_paper_transition_symbol: 'BTCUSDT',
      last_paper_transition_occurred_ts_ms: 1_759_888_000_000,
      last_paper_transition_reversible: false,
    },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => transitionedDashboard })),
  )
  vi.stubGlobal('WebSocket', FakeWebSocket)

  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: '설정' }))

  expect(await screen.findByText('마지막 PAPER 상태')).toBeInTheDocument()
  expect(screen.getByText('포지션 보호 중')).toBeInTheDocument()
  expect(screen.getByText('BTCUSDT · 공동 PAPER 계좌')).toBeInTheDocument()
  fireEvent.click(screen.getByText('고급 진단 보기'))
  expect(screen.getByText('마지막 PAPER 전환 결과')).toBeInTheDocument()
  expect(screen.getByText('ENTRY_FILLED')).toBeInTheDocument()
  expect(screen.getByText('PAPER · 실제 주문 0')).toBeInTheDocument()
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
    operation_status: {
      ...initialDashboard.operation_status,
      state: 'DEMO_RUNNING' as const,
      title_ko: '샘플 작동 중',
      detail_ko: '저장된 샘플 PAPER 화면이며 실제 공개시장은 아닙니다.',
      market_observation_active: true,
      paper_entry_active: true,
      automatic_recovery: false,
      recommended_action: 'PAUSE' as const,
    },
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
  expect(screen.getByRole('button', { name: '샘플 멈춤' })).toBeInTheDocument()
})

test('renders an explicit LIVE operating state', async () => {
  const liveDashboard = {
    ...initialDashboard,
    status: {
      ...initialDashboard.status,
      mode: 'LIVE_SHADOW_PAPER' as const,
      market_data_state: 'LIVE' as const,
      venue: 'BINANCE_USDM',
      health_flags: ['PUBLIC_SUPERVISOR_RUNNING', 'NO_AUTH_HEADERS'],
    },
    operation_status: {
      ...initialDashboard.operation_status,
      state: 'RUNNING' as const,
      title_ko: '작동 중',
      detail_ko: '공개시장을 계속 관찰하며 조건이 맞을 때만 PAPER 진입을 기록합니다.',
      market_observation_active: true,
      paper_entry_active: true,
      recommended_action: 'PAUSE' as const,
      lag_p95_ms: 120,
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

  expect(await screen.findByLabelText('프로그램 작동 상태')).toHaveTextContent('작동 중')
  expect(screen.getByLabelText('프로그램 작동 상태')).toHaveTextContent('시장 관찰계속 작동')
  expect(screen.getByLabelText('프로그램 작동 상태')).toHaveTextContent('새 PAPER 진입작동')
})

test('clears a previous run PAPER entry notice when the run changes', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => key === 'robom.position.focus.v1'
      ? JSON.stringify({ autoFocusOnFill: false, focusLocked: false, defaultProfile: 'BASE' })
      : null),
    setItem: vi.fn(),
  })
  const position = {
    focus_key: 'run-live:trade-new', trade_id: 'trade-new', candidate_id: 'candidate-new',
    account_id: 'LSA_REVERSAL_V1:BASE', profile: 'BASE', venue: 'BINANCE_USDM',
    symbol: 'BTCUSDT', side: 'LONG', strategy: 'LSA_REVERSAL_V1', strategy_id: 'LSA_REVERSAL_V1',
    strategy_display_name_ko: '급락·급등 쓸기 반전', opened_ts_ms: 2, signal_ts_ms: 1,
    auto_focus_eligible: true,
  } as unknown as FocusPosition
  const handlers = {
    onChartChange: vi.fn(), onStartLive: vi.fn(), onStartDemo: vi.fn(),
    onPauseToggle: vi.fn(), busy: false, operation: null, onCancel: vi.fn(), onRetry: vi.fn(),
  }
  const { rerender } = render(<MarketPage data={initialDashboard} {...handlers} />)
  const liveDashboard = {
    ...initialDashboard,
    status: { ...initialDashboard.status, mode: 'LIVE_SHADOW_PAPER' as const, run_id: 'run-live' },
    focus_positions: [position],
  }

  rerender(<MarketPage data={liveDashboard} {...handlers} />)
  expect(await screen.findByText(/새 PAPER 진입 · BTCUSDT/)).toBeInTheDocument()

  rerender(<MarketPage data={initialDashboard} {...handlers} />)
  await waitFor(() => expect(screen.queryByText(/새 PAPER 진입 · BTCUSDT/)).not.toBeInTheDocument())
})

test('distinguishes shared and independent BASE positions in the live list', () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => key === 'robom.position.focus.v1'
      ? JSON.stringify({ autoFocusOnFill: false, focusLocked: false, defaultProfile: 'BASE' })
      : null),
    setItem: vi.fn(),
  })
  const common = {
    candidate_id: 'candidate-same', profile: 'BASE', venue: 'BINANCE_USDM',
    symbol: 'XRPUSDT', side: 'LONG', strategy: 'LSA_REVERSAL_V1', strategy_id: 'LSA_REVERSAL_V1',
    strategy_display_name_ko: '급락·급등 쓸기 반전', opened_ts_ms: 2, signal_ts_ms: 1,
    auto_focus_eligible: true, stage_ko: '익절·손절 보호 중', net_pnl_usdt: '-0.1',
  }
  const shared = {
    ...common, focus_key: 'MAIN:trade-shared', trade_id: 'trade-shared', account_id: 'SHARED_PAPER',
  } as unknown as FocusPosition
  const independent = {
    ...common, focus_key: 'LSA:trade-independent', trade_id: 'trade-independent', account_id: 'LSA_REVERSAL_V1:BASE',
  } as unknown as FocusPosition
  const handlers = {
    onChartChange: vi.fn(), onStartLive: vi.fn(), onStartDemo: vi.fn(),
    onPauseToggle: vi.fn(), busy: false, operation: null, onCancel: vi.fn(), onRetry: vi.fn(),
  }
  render(<MarketPage data={{
    ...initialDashboard,
    status: { ...initialDashboard.status, mode: 'LIVE_SHADOW_PAPER' as const, run_id: 'run-live' },
    focus_positions: [shared, independent],
  }} {...handlers} />)

  expect(screen.getByRole('button', { name: /XRPUSDT.*BASE.*공동계좌/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /XRPUSDT.*BASE.*전략 독립계좌/ })).toBeInTheDocument()
})

test('clears a PAPER entry notice when READY reuses the same run id', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => key === 'robom.position.focus.v1'
      ? JSON.stringify({ autoFocusOnFill: false, focusLocked: false, defaultProfile: 'BASE' })
      : null),
    setItem: vi.fn(),
  })
  const position = {
    focus_key: 'ready:trade-reused', trade_id: 'trade-reused', candidate_id: 'candidate-reused',
    account_id: 'LSA_REVERSAL_V1:BASE', profile: 'BASE', venue: 'BINANCE_USDM',
    symbol: 'BTCUSDT', side: 'LONG', strategy: 'LSA_REVERSAL_V1', strategy_id: 'LSA_REVERSAL_V1',
    strategy_display_name_ko: '급락·급등 쓸기 반전', opened_ts_ms: 2, signal_ts_ms: 1,
    auto_focus_eligible: true,
  } as unknown as FocusPosition
  const handlers = {
    onChartChange: vi.fn(), onStartLive: vi.fn(), onStartDemo: vi.fn(),
    onPauseToggle: vi.fn(), busy: false, operation: null, onCancel: vi.fn(), onRetry: vi.fn(),
  }
  const liveSameRun = {
    ...initialDashboard,
    status: { ...initialDashboard.status, mode: 'LIVE_SHADOW_PAPER' as const },
    focus_positions: [position],
  }
  const { rerender } = render(<MarketPage data={initialDashboard} {...handlers} />)

  rerender(<MarketPage data={liveSameRun} {...handlers} />)

  expect(await screen.findByText(/새 PAPER 진입 · BTCUSDT/)).toBeInTheDocument()

  rerender(<MarketPage data={initialDashboard} {...handlers} />)
  await waitFor(() => expect(screen.queryByText(/새 PAPER 진입 · BTCUSDT/)).not.toBeInTheDocument())
})

test('relabels a PAPER entry notice when the position closes in the same live run', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => key === 'robom.position.focus.v1'
      ? JSON.stringify({ autoFocusOnFill: true, focusLocked: false, defaultProfile: 'BASE' })
      : null),
    setItem: vi.fn(),
  })
  const position = {
    focus_key: 'run-live:trade-closed', trade_id: 'trade-closed', candidate_id: 'candidate-closed',
    account_id: 'LSA_REVERSAL_V1:BASE', profile: 'BASE', venue: 'BINANCE_USDM',
    symbol: 'BTCUSDT', side: 'LONG', strategy: 'LSA_REVERSAL_V1', strategy_id: 'LSA_REVERSAL_V1',
    strategy_display_name_ko: '급락·급등 쓸기 반전', opened_ts_ms: 2, signal_ts_ms: 1,
    auto_focus_eligible: true,
  } as unknown as FocusPosition
  const handlers = {
    onChartChange: vi.fn(), onStartLive: vi.fn(), onStartDemo: vi.fn(),
    onPauseToggle: vi.fn(), busy: false, operation: null, onCancel: vi.fn(), onRetry: vi.fn(),
  }
  const liveDashboard = {
    ...initialDashboard,
    status: { ...initialDashboard.status, mode: 'LIVE_SHADOW_PAPER' as const, run_id: 'run-live' },
    focus_positions: [position],
  }
  const { rerender } = render(<MarketPage data={initialDashboard} {...handlers} />)

  rerender(<MarketPage data={liveDashboard} {...handlers} />)
  expect(await screen.findByText(/새 PAPER 진입 · BTCUSDT/)).toBeInTheDocument()

  rerender(<MarketPage data={{ ...liveDashboard, focus_positions: [] }} {...handlers} />)
  expect(await screen.findByText(/PAPER 거래 종료 · BTCUSDT · 급락·급등 쓸기 반전 · BASE/)).toBeInTheDocument()
  expect(screen.queryByText(/새 PAPER 진입 · BTCUSDT/)).not.toBeInTheDocument()
})

test('shows the verified public venue clock correction in system diagnostics', async () => {
  const serverNow = Date.now()
  const clockDashboard = {
    ...initialDashboard,
    system: {
      ...initialDashboard.system,
      server_time_ms: serverNow,
      venue_clock_offset_ms: 2011.5,
      venue_clock_rtt_ms: 43,
      clock_sync_status: 'SYNCED',
    },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => clockDashboard })),
  )
  class ClockWebSocket extends EventTarget {
    close() {}
    constructor() {
      super()
      queueMicrotask(() => {
        this.dispatchEvent(new Event('open'))
        this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'dashboard', data: clockDashboard }) }))
      })
    }
  }
  vi.stubGlobal('WebSocket', ClockWebSocket)

  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: '설정' }))

  expect(await screen.findByText(/거래소 시각 \+2012ms 보정/)).toBeInTheDocument()
  fireEvent.click(screen.getByText('고급 진단 보기'))
  expect(screen.getByText('거래소 시각 동기화')).toBeInTheDocument()
  expect(screen.getByText('SYNCED')).toBeInTheDocument()
})
