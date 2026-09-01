// V6 WebSocket snapshot·delta·heartbeat·family 선택 계약을 검증한다.
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useDashboard } from '../src/hooks/useDashboard'
import { dashboardFixture, paperResearchSettings } from './fixtures'

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

function DashboardProbe() {
  const {
    data,
    connected,
    safetyVerified,
    selectedFamilyId,
    selectedFamilyDetail,
    requestError,
    control,
    configureStrategyFamilyResearch,
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
      <span data-testid="safety-verified">{safetyVerified ? 'VERIFIED' : 'UNVERIFIED'}</span>
      <span data-testid="selected-family">{selectedFamilyId ?? 'NONE'}</span>
      <span data-testid="selected-detail">{selectedFamilyDetail?.label_ko ?? 'NONE'}</span>
      <span data-testid="enabled-entry-count">{data.strategy_family_catalog?.inventory?.enabled_directional_entry_candidate_count ?? -1}</span>
      <span data-testid="request-error">{requestError || 'NONE'}</span>
      <button type="button" onClick={() => void control('start-demo').catch(() => undefined)}>
        샘플 제어 요청
      </button>
      <button
        type="button"
        onClick={() => selectStrategyFamily('TREND_PULLBACK')}
      >
        추세 family 선택
      </button>
      <button
        type="button"
        onClick={() => void configureStrategyFamilyResearch('TREND_PULLBACK', {
          research_enabled: false,
          expected_revision: 1,
          reason: 'TEST_RESEARCH_OFF',
        }).catch(() => undefined)}
      >
        추세 모의평가 끄기
      </button>
    </main>
  )
}

function SettingsProbe() {
  const { data, safetyVerified } = useDashboard('settings')
  return <>
    <span data-testid="startup-recovery">{String(data.system.startup_recovery_state ?? 'MISSING')}</span>
    <span data-testid="diagnostic-label">{data.diagnostics?.rows[0]?.label_ko ?? 'MISSING'}</span>
    <span data-testid="settings-safety-verified">{safetyVerified ? 'VERIFIED' : 'UNVERIFIED'}</span>
  </>
}

function StrategySafetyProbe() {
  const { data, safetyVerified } = useDashboard('strategies')
  return <>
    <span data-testid="strategy-page-safety">{safetyVerified ? 'VERIFIED' : 'UNVERIFIED'}</span>
    <span data-testid="strategy-page-count">{data.strategies.length}</span>
    <span data-testid="strategy-family-count">{data.strategy_family_catalog?.families.length ?? -1}</span>
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
          ...flatPaperSafety,
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
      status: { ...dashboard.status, run_id: 'run-v6-snapshot' },
      paper_entry_intent: dashboard.paper_entry_intent,
      paper_only: true,
      real_orders_enabled: false,
      auth_required: false,
      private_api_enabled: false,
      api_key_enabled: false,
      wallet_enabled: false,
      runtime_ai_order_decision_enabled: false,
      funding_readiness: 'NOT_READY',
      strategy_state: [{ strategy_id: strategyId, mode: 'OFF' }],
    },
  }))
  expect(screen.getByTestId('run-id')).toHaveTextContent('run-v6-snapshot')
  expect(screen.getByTestId('strategy-mode')).toHaveTextContent('OFF')
  expect(screen.getByTestId('safety-verified')).toHaveTextContent('VERIFIED')

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
        ...flatPaperSafety,
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

  act(() => socket.message({
    schema_version: 1,
    sequence: 8,
    type: 'summary_delta',
    data: { real_orders_enabled: true },
  }))
  expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED')
  expect(screen.getByTestId('connection')).toHaveTextContent('DISCONNECTED')
  expect(screen.getByTestId('run-id')).toHaveTextContent('run-v6-delta')
})

test('rejects an unsafe strategy row delta without merging the strategy change', async () => {
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
          ...flatPaperSafety,
        }
      : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('VERIFIED'))
  const socket = FakeWebSocket.instances[0]
  act(() => socket.open())
  act(() => socket.message({
    schema_version: 1,
    sequence: 1,
    type: 'snapshot',
    data: {
      status: dashboard.status,
      paper_entry_intent: dashboard.paper_entry_intent,
      strategy_state: [{ strategy_id: strategyId, mode: 'OFF' }],
      ...flatPaperSafety,
    },
  }))
  expect(screen.getByTestId('strategy-mode')).toHaveTextContent('OFF')

  act(() => socket.message({
    schema_version: 1,
    sequence: 2,
    type: 'strategy_row_delta',
    data: {
      rows: [{ strategy_id: strategyId, mode: 'ACTIVE', private_api_enabled: true }],
      removed_strategy_ids: [],
    },
  }))

  expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED')
  expect(screen.getByTestId('connection')).toHaveTextContent('DISCONNECTED')
  expect(screen.getByTestId('strategy-mode')).toHaveTextContent('OFF')
})

test('rejects a selected family detail with an incomplete PAPER safety contract', async () => {
  const dashboard = dashboardFixture()
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
          ...flatPaperSafety,
        }
      : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('VERIFIED'))
  const socket = FakeWebSocket.instances[0]
  act(() => socket.open())
  fireEvent.click(screen.getByRole('button', { name: '추세 family 선택' }))
  act(() => socket.message({
    schema_version: 1,
    sequence: 1,
    type: 'selected_detail_delta',
    data: {
      family_id: 'TREND_PULLBACK',
      detail: {
        family_id: 'TREND_PULLBACK',
        label_ko: '노출되면 안 되는 family',
        variants: [],
        paper_only: true,
        real_orders_enabled: false,
        auth_required: false,
      },
    },
  }))

  expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED')
  expect(screen.getByTestId('connection')).toHaveTextContent('DISCONNECTED')
  expect(screen.getByTestId('selected-detail')).toHaveTextContent('NONE')
})

test('invalidates safety when a family research mutation response contradicts PAPER safety', async () => {
  const dashboard = dashboardFixture()
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path.endsWith('/research-enabled')
      ? {
          family_id: 'TREND_PULLBACK',
          variants: [],
          ...flatPaperSafety,
          private_api_enabled: true,
        }
      : path === '/api/strategies/summary'
        ? {
            schema_version: 1,
            analysis_scope: 'CURRENT_STRATEGY_VERSION',
            strategies: dashboard.strategies,
            league_accounts: dashboard.league_accounts,
            strategy_count: dashboard.strategies.length,
            league_account_count: dashboard.league_accounts.length,
            ...flatPaperSafety,
          }
        : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('VERIFIED'))
  act(() => FakeWebSocket.instances[0].open())
  fireEvent.click(screen.getByRole('button', { name: '추세 모의평가 끄기' }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/strategy-families/TREND_PULLBACK/research-enabled',
    expect.objectContaining({ method: 'PATCH' }),
  ))
  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED'))
  expect(screen.getByTestId('selected-detail')).toHaveTextContent('NONE')
})

test('refreshes the inventory without HTTP cache after a family research mutation', async () => {
  const dashboard = dashboardFixture()
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path.endsWith('/research-enabled')
      ? {
          family_id: 'TREND_PULLBACK',
          variants: [],
          ...flatPaperSafety,
        }
      : path === '/api/strategy-families'
        ? {
            schema_version: 1,
            families: [],
            inventory: {
              schema: 'flowscalper.strategy_inventory.v1',
              registered_catalog_item_count: 16,
              runtime_registry_variant_count: 15,
              enabled_directional_entry_candidate_count: 5,
              current_family_entry_representative_count: 3,
              inactive_history_runtime_variant_count: 10,
              catalog_virtual_filter_count: 1,
              active_directional_entry_count: 0,
            },
            ...flatPaperSafety,
          }
        : path === '/api/strategies/summary'
          ? {
              schema_version: 1,
              analysis_scope: 'CURRENT_STRATEGY_VERSION',
              strategies: dashboard.strategies,
              league_accounts: dashboard.league_accounts,
              strategy_count: dashboard.strategies.length,
              league_account_count: dashboard.league_accounts.length,
              ...flatPaperSafety,
            }
          : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('VERIFIED'))
  act(() => FakeWebSocket.instances[0].open())
  fireEvent.click(screen.getByRole('button', { name: '추세 모의평가 끄기' }))

  await waitFor(() => expect(screen.getByTestId('enabled-entry-count')).toHaveTextContent('5'))
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/strategy-families',
    expect.objectContaining({ cache: 'no-store' }),
  )
  expect(screen.getByTestId('request-error')).toHaveTextContent('NONE')
})

test('does not send mutation requests before PAPER safety is verified', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    void input
    void init
    return new Promise<Response>(() => undefined)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  fireEvent.click(screen.getByRole('button', { name: '샘플 제어 요청' }))
  fireEvent.click(screen.getByRole('button', { name: '추세 모의평가 끄기' }))

  await waitFor(() => expect(screen.getByTestId('request-error')).toHaveTextContent('PAPER 안전 상태를 확인할 때까지'))
  expect(fetchMock).toHaveBeenCalledWith('/api/ui/summary', expect.any(Object))
  expect(fetchMock.mock.calls.some(([path, init]) => (
    String(path).startsWith('/api/control/')
    || String(path).endsWith('/research-enabled')
    || (typeof init === 'object' && init !== null && 'method' in init && init.method === 'POST')
  ))).toBe(false)
})

test('does not infer PAPER-only safety when only paper_only is missing', async () => {
  const dashboard = dashboardFixture()
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void input
    void init
    return new Response(JSON.stringify({
      schema_version: 1,
      status: dashboard.status,
      paper_entry_intent: dashboard.paper_entry_intent,
      real_orders_enabled: false,
      auth_required: false,
      private_api_enabled: false,
      api_key_enabled: false,
      wallet_enabled: false,
      runtime_ai_order_decision_enabled: false,
      funding_readiness: 'NOT_READY',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)

  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED'))
  fireEvent.click(screen.getByRole('button', { name: '샘플 제어 요청' }))
  await waitFor(() => expect(screen.getByTestId('request-error')).toHaveTextContent('PAPER 안전 상태를 확인할 때까지'))
  expect(fetchMock.mock.calls.some(([path, init]) => (
    String(path).startsWith('/api/control/')
    || (typeof init === 'object' && init !== null && 'method' in init && init.method === 'POST')
  ))).toBe(false)
})

test.each([
  ['strategy summary', '/api/strategies/summary'],
  ['family catalog', '/api/strategy-families'],
] as const)(
  'invalidates safety and does not merge a contradictory %s response',
  async (_label, deferredPath) => {
    const dashboard = dashboardFixture()
    let resolveDeferred: ((response: Response) => void) | undefined
    const deferredResponse = new Promise<Response>((resolve) => {
      resolveDeferred = resolve
    })
    const safeStrategySummary = {
      schema_version: 1,
      analysis_scope: 'CURRENT_STRATEGY_VERSION',
      strategies: dashboard.strategies,
      league_accounts: dashboard.league_accounts,
      strategy_count: dashboard.strategies.length,
      league_account_count: dashboard.league_accounts.length,
      ...flatPaperSafety,
    }
    const safeFamilyCatalog = {
      schema_version: 1,
      families: [],
      ...flatPaperSafety,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === deferredPath) return deferredResponse
      const body = path === '/api/strategies/summary'
        ? safeStrategySummary
        : path === '/api/strategy-families'
          ? safeFamilyCatalog
          : dashboard
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<StrategySafetyProbe />)
    await waitFor(() => expect(screen.getByTestId('strategy-page-safety')).toHaveTextContent('VERIFIED'))
    const strategyCountBefore = screen.getByTestId('strategy-page-count').textContent
    const familyCountBefore = screen.getByTestId('strategy-family-count').textContent

    const invalidBody = deferredPath === '/api/strategies/summary'
      ? { ...safeStrategySummary, strategies: [], private_api_enabled: 'NOT_PROVEN' }
      : { ...safeFamilyCatalog, families: [], private_api_enabled: 'NOT_PROVEN' }
    await act(async () => {
      resolveDeferred?.(new Response(JSON.stringify(invalidBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      await deferredResponse
    })

    await waitFor(() => expect(screen.getByTestId('strategy-page-safety')).toHaveTextContent('UNVERIFIED'))
    expect(screen.getByTestId('strategy-page-count')).toHaveTextContent(strategyCountBefore ?? '')
    expect(screen.getByTestId('strategy-family-count')).toHaveTextContent(familyCountBefore ?? '')
  },
)

test('does not let a stale HTTP response reverify safety after an unsafe socket message', async () => {
  const dashboard = dashboardFixture()
  let resolveSummary: ((response: Response) => void) | undefined
  const deferredSummary = new Promise<Response>((resolve) => {
    resolveSummary = resolve
  })
  const fetchMock = vi.fn((input: RequestInfo | URL) => (
    String(input) === '/api/ui/summary'
      ? deferredSummary
      : new Promise<Response>(() => undefined)
  ))
  vi.stubGlobal('fetch', fetchMock)

  render(<DashboardProbe />)
  const socket = FakeWebSocket.instances[0]
  act(() => socket.open())
  act(() => socket.message({
    schema_version: 1,
    sequence: 1,
    type: 'summary_delta',
    data: { real_orders_enabled: true },
  }))

  await act(async () => {
    resolveSummary?.(new Response(JSON.stringify({
      schema_version: 1,
      status: { ...dashboard.status, run_id: 'stale-safe-http' },
      paper_entry_intent: dashboard.paper_entry_intent,
      paper_only: true,
      real_orders_enabled: false,
      auth_required: false,
      private_api_enabled: false,
      api_key_enabled: false,
      wallet_enabled: false,
      runtime_ai_order_decision_enabled: false,
      funding_readiness: 'NOT_READY',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await deferredSummary
  })

  await waitFor(() => expect(screen.getByTestId('safety-verified')).toHaveTextContent('UNVERIFIED'))
  expect(screen.getByTestId('connection')).toHaveTextContent('DISCONNECTED')
  expect(screen.getByTestId('run-id')).not.toHaveTextContent('stale-safe-http')
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
            private_api_enabled: false,
            api_key_enabled: false,
            wallet_enabled: false,
            runtime_ai_order_decision_enabled: false,
            entry_state: dashboard.paper_entry_intent.state,
            entry_revision: dashboard.paper_entry_intent.revision,
            active_locks: dashboard.risk.active_locks,
          },
          costs: dashboard.risk.strategy_league,
          paper_research: paperResearchSettings(),
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
            ...flatPaperSafety,
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

test('invalidates verified safety when settings deny the public-market-only contract', async () => {
  const dashboard = dashboardFixture()
  let resolveSettings: ((response: Response) => void) | undefined
  const deferredSettings = new Promise<Response>((resolve) => {
    resolveSettings = resolve
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/api/settings/summary') return deferredSettings
    const body = path === '/api/diagnostics'
      ? {
          schema_version: 1,
          rows: [],
          raw: {},
          ...flatPaperSafety,
        }
      : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<SettingsProbe />)
  await waitFor(() => expect(screen.getByTestId('settings-safety-verified')).toHaveTextContent('VERIFIED'))

  await act(async () => {
    resolveSettings?.(new Response(JSON.stringify({
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
        private_api_enabled: false,
        api_key_enabled: false,
        wallet_enabled: false,
        runtime_ai_order_decision_enabled: false,
        entry_state: dashboard.paper_entry_intent.state,
        entry_revision: dashboard.paper_entry_intent.revision,
        active_locks: dashboard.risk.active_locks,
      },
      costs: dashboard.risk.strategy_league,
      paper_research: paperResearchSettings(),
      storage: {},
      connection: { public_market_only: false },
      autostart: {
        state: 'NOT_PROVEN',
        launch_agent_verified: false,
        read_only: true,
        evidence_source: 'LAUNCH_AGENT_NOT_INSPECTED',
        evidence_ko: '공개시장 전용 계약이 아니므로 병합하지 않습니다.',
      },
      local_preferences: {
        research_detail_default: false,
        research_detail_affects_execution: false,
      },
      funding_readiness: 'NOT_READY',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await deferredSettings
  })

  await waitFor(() => expect(screen.getByTestId('settings-safety-verified')).toHaveTextContent('UNVERIFIED'))
})

test('invalidates verified safety and rejects contradictory auxiliary diagnostics', async () => {
  const dashboard = dashboardFixture()
  let resolveDiagnostics: ((response: Response) => void) | undefined
  const deferredDiagnostics = new Promise<Response>((resolve) => {
    resolveDiagnostics = resolve
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/api/diagnostics') return deferredDiagnostics
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
            private_api_enabled: false,
            api_key_enabled: false,
            wallet_enabled: false,
            runtime_ai_order_decision_enabled: false,
            entry_state: dashboard.paper_entry_intent.state,
            entry_revision: dashboard.paper_entry_intent.revision,
            active_locks: dashboard.risk.active_locks,
          },
          costs: dashboard.risk.strategy_league,
          paper_research: paperResearchSettings(),
          storage: {},
          connection: { public_market_only: true },
          autostart: {
            state: 'NOT_PROVEN',
            launch_agent_verified: false,
            read_only: true,
            evidence_source: 'LAUNCH_AGENT_NOT_INSPECTED',
            evidence_ko: 'PAPER 상태 자동 복구는 macOS 자동 시작의 증거가 아닙니다.',
          },
          local_preferences: {
            research_detail_default: false,
            research_detail_affects_execution: false,
          },
          funding_readiness: 'NOT_READY',
        }
      : dashboard
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<SettingsProbe />)

  await waitFor(() => expect(screen.getByTestId('settings-safety-verified')).toHaveTextContent('VERIFIED'))
  await act(async () => {
    resolveDiagnostics?.(new Response(JSON.stringify({
      schema_version: 1,
      rows: [{
        key: 'startup_recovery_state',
        label_ko: '합성되면 안 되는 모순 진단',
        value: 'UNSAFE_RAW',
        severity: 'CRITICAL',
        user_visible: false,
        group: 'RUNTIME',
      }],
      raw: {
        startup_recovery_state: 'UNSAFE_RAW',
        private_api_enabled: true,
      },
      ...flatPaperSafety,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await deferredDiagnostics
  })

  await waitFor(() => expect(screen.getByTestId('settings-safety-verified')).toHaveTextContent('UNVERIFIED'))
  expect(screen.getByTestId('diagnostic-label')).not.toHaveTextContent('합성되면 안 되는 모순 진단')
  expect(screen.getByTestId('startup-recovery')).not.toHaveTextContent('UNSAFE_RAW')
})
