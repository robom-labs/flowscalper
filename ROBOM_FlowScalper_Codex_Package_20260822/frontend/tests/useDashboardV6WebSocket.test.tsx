// V6 WebSocket snapshot·delta·heartbeat·family 선택 계약을 검증한다.
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useDashboard } from '../src/hooks/useDashboard'
import { dashboardFixture } from './fixtures'

class FakeWebSocket extends EventTarget {
  static instances: FakeWebSocket[] = []
  readonly url: string
  readonly sent: string[] = []
  readyState = 0

  constructor(url: string) {
    super()
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = 1
    this.dispatchEvent(new Event('open'))
  }

  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.dispatchEvent(new Event('close'))
  }

  send(payload: string) {
    this.sent.push(payload)
  }

  message(payload: unknown) {
    this.dispatchEvent(
      new MessageEvent('message', { data: JSON.stringify(payload) }),
    )
  }
}

function DashboardProbe() {
  const {
    data,
    connected,
    selectedFamilyId,
    selectedFamilyDetail,
    selectStrategyFamily,
  } = useDashboard('market')
  const firstStrategy = data.strategies[0]
  return (
    <main>
      <span data-testid="run-id">{data.status.run_id}</span>
      <span data-testid="strategy-mode">{firstStrategy?.mode ?? 'NONE'}</span>
      <span data-testid="position-symbol">{data.position?.symbol ?? 'NONE'}</span>
      <span data-testid="chart-points">{data.chart.points.length}</span>
      <span data-testid="connection">{connected ? 'CONNECTED' : 'DISCONNECTED'}</span>
      <span data-testid="selected-family">{selectedFamilyId ?? 'NONE'}</span>
      <span data-testid="selected-detail">{selectedFamilyDetail?.label_ko ?? 'NONE'}</span>
      <button
        type="button"
        onClick={() => selectStrategyFamily('TREND_PULLBACK')}
      >
        추세 family 선택
      </button>
    </main>
  )
}

function SettingsProbe() {
  const { data } = useDashboard('settings')
  return <>
    <span data-testid="startup-recovery">{String(data.system.startup_recovery_state ?? 'MISSING')}</span>
    <span data-testid="diagnostic-label">{data.diagnostics?.rows[0]?.label_ko ?? 'MISSING'}</span>
  </>
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('applies ordered V6 deltas and sends family selection without reconnecting on heartbeat', async () => {
  const dashboard = dashboardFixture()
  const strategyId = dashboard.strategies[0].strategy_id
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path === '/api/strategies/summary'
      ? {
          schema_version: 1,
          analysis_scope: 'CURRENT_STRATEGY_VERSION',
          strategies: dashboard.strategies,
          league_accounts: dashboard.league_accounts,
          strategy_count: dashboard.strategies.length,
          league_account_count: dashboard.league_accounts.length,
          paper_only: true,
          real_orders_enabled: false,
          auth_required: false,
        }
      : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)

  await waitFor(() => expect(screen.getByTestId('run-id')).toHaveTextContent(dashboard.status.run_id))
  await waitFor(() => expect(screen.getByTestId('strategy-mode')).not.toHaveTextContent('NONE'))
  expect(fetchMock.mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining([
    '/api/ui/summary',
    '/api/strategies/summary',
  ]))
  expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/dashboard')).toBe(false)
  const socket = FakeWebSocket.instances[0]
  expect(socket.url).toMatch(/\/ws\/ui$/)
  act(() => socket.open())

  act(() => socket.message({
    schema_version: 1,
    sequence: 1,
    type: 'snapshot',
    data: {
      status: { run_id: 'run-v6-snapshot' },
      strategy_state: [{ strategy_id: strategyId, mode: 'OFF' }],
    },
  }))
  expect(screen.getByTestId('run-id')).toHaveTextContent('run-v6-snapshot')
  expect(screen.getByTestId('strategy-mode')).toHaveTextContent('OFF')

  act(() => socket.message({
    schema_version: 1,
    sequence: 2,
    type: 'summary_delta',
    data: { status: { run_id: 'run-v6-delta' } },
  }))
  act(() => socket.message({
    schema_version: 1,
    sequence: 3,
    type: 'strategy_row_delta',
    data: {
      rows: [{ strategy_id: strategyId, mode: 'SHADOW' }],
      removed_strategy_ids: [],
    },
  }))
  act(() => socket.message({
    schema_version: 1,
    sequence: 4,
    type: 'position_delta',
    data: { position: { symbol: 'ETHUSDT' } },
  }))
  act(() => socket.message({
    schema_version: 1,
    sequence: 5,
    type: 'chart_delta',
    data: {
      symbol: dashboard.chart.symbol,
      interval: dashboard.chart.interval,
      fixture: dashboard.chart.fixture,
      refresh_required: false,
      point_upserts: [{ index: 0, ts_ms: 1234, bid: 100, ask: 101, mid: 100.5, microprice: 100.6 }],
      removed_point_ts_ms: [],
      candle_upserts: [],
      removed_candle_open_ts_ms: [],
    },
  }))
  act(() => socket.message({
    schema_version: 1,
    sequence: 6,
    type: 'heartbeat',
    data: { server_ts_ms: 1234 },
  }))

  expect(screen.getByTestId('run-id')).toHaveTextContent('run-v6-delta')
  expect(screen.getByTestId('strategy-mode')).toHaveTextContent('SHADOW')
  expect(screen.getByTestId('position-symbol')).toHaveTextContent('ETHUSDT')
  expect(screen.getByTestId('chart-points')).toHaveTextContent('1')
  expect(screen.getByTestId('connection')).toHaveTextContent('CONNECTED')
  expect(FakeWebSocket.instances).toHaveLength(1)

  fireEvent.click(screen.getByRole('button', { name: '추세 family 선택' }))
  expect(screen.getByTestId('selected-family')).toHaveTextContent('TREND_PULLBACK')
  expect(JSON.parse(socket.sent.at(-1) ?? '{}')).toEqual({
    type: 'select_family',
    family_id: 'TREND_PULLBACK',
  })

  act(() => socket.message({
    schema_version: 1,
    sequence: 7,
    type: 'selected_detail_delta',
    data: {
      family_id: 'TREND_PULLBACK',
      detail: {
        family_id: 'TREND_PULLBACK',
        label_ko: '추세 눌림·재합류',
        variants: [],
        offline_challengers: [],
      },
    },
  }))
  expect(screen.getByTestId('selected-detail')).toHaveTextContent('추세 눌림·재합류')

  act(() => socket.message({
    schema_version: 1,
    sequence: 2,
    type: 'summary_delta',
    data: { status: { run_id: 'stale-message' } },
  }))
  expect(screen.getByTestId('run-id')).toHaveTextContent('run-v6-delta')
})

test('loads raw diagnostics only after the settings page is selected', async () => {
  const dashboard = dashboardFixture()
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path === '/api/settings/summary'
      ? {
          schema_version: 1,
          run: {
            run_id: dashboard.status.run_id,
            mode: dashboard.status.mode,
            venue: dashboard.status.venue,
            new_run_preserves_history: true,
          },
          safety: {
            paper_only: true,
            real_orders_enabled: false,
            auth_required: false,
            entry_state: dashboard.paper_entry_intent.state,
            entry_revision: dashboard.paper_entry_intent.revision,
            active_locks: dashboard.risk.active_locks,
          },
          costs: dashboard.risk.strategy_league,
          storage: {},
          connection: { public_market_only: true },
          autostart: {
            state: 'NOT_PROVEN',
            paper_state_recovery_reported: true,
            launch_agent_verified: false,
            read_only: true,
            evidence_source: 'LAUNCH_AGENT_NOT_INSPECTED',
            evidence_ko: 'PAPER 상태 자동 복구는 macOS 로그인·재부팅 자동 시작의 증거가 아닙니다.',
          },
          local_preferences: {
            research_detail_default: false,
            research_detail_affects_execution: false,
          },
          funding_readiness: 'NOT_READY',
        }
      : path === '/api/diagnostics'
        ? {
            schema_version: 1,
            rows: [{
              key: 'startup_recovery_state',
              label_ko: 'Backend가 제공한 시작 복구 상태',
              value: 'FIXTURE_STATE_RECOVERED',
              severity: 'INFO',
              user_visible: false,
              group: 'RUNTIME',
            }],
            raw: { startup_recovery_state: 'FIXTURE_STATE_RECOVERED' },
            paper_only: true,
            real_orders_enabled: false,
            auth_required: false,
          }
        : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<SettingsProbe />)

  await waitFor(() => expect(screen.getByTestId('startup-recovery')).toHaveTextContent('FIXTURE_STATE_RECOVERED'))
  expect(screen.getByTestId('diagnostic-label')).toHaveTextContent('Backend가 제공한 시작 복구 상태')
  expect(fetchMock.mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining([
    '/api/ui/summary',
    '/api/settings/summary',
    '/api/diagnostics',
  ]))
  expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/dashboard')).toBe(false)
})
