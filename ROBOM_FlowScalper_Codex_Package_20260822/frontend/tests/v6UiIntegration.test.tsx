// V6 거래 opportunity와 전략 family 조건·주문흐름 필터 계약을 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { StrategiesPage } from '../src/pages/StrategiesPage'
import { SettingsPage } from '../src/pages/SettingsPage'
import { TradesPage } from '../src/pages/TradesPage'
import type { HistoryRow, TradesResponse } from '../src/types'
import { dashboardFixture, leagueAccounts, paperResearchSettings, strategies } from './fixtures'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function emptyConditions(familyId: string) {
  return {
    schema_version: 1,
    family_id: familyId,
    strategy_id: null,
    symbol: 'BTCUSDT',
    setup_state: 'RESEARCH_NOT_IMPLEMENTED',
    passed: 0,
    total: 0,
    top_blockers: [],
    conditions: [],
    sides: [],
    execution: {},
    paper_only: true,
    real_orders_enabled: false,
    auth_required: false,
    private_api_enabled: false,
    api_key_enabled: false,
    wallet_enabled: false,
    runtime_ai_order_decision_enabled: false,
    funding_readiness: 'NOT_READY',
  }
}

function groupedTradesResponse(row?: HistoryRow): TradesResponse {
  const opportunities = row ? [{
    key: {
      run_id: row.run_id,
      strategy_id: row.strategy,
      strategy_version: row.strategy_version ?? 'UNKNOWN',
      opportunity_id: row.opportunity_id ?? 'opportunity-refresh',
      symbol: row.symbol,
      side: row.side,
    },
    family_id: 'BREAKOUT_RUNNER',
    family_label_ko: '돌파·큰 추세',
    variant_label_ko: '현재 variant',
    entry_ts_ms: row.entry_ts_ms,
    exit_ts_ms: row.exit_ts_ms,
    profiles: { [row.profile]: row },
    account_groups: [{
      account_scope: row.account_scope ?? 'MAIN',
      account_group_id: row.account_scope === 'LEAGUE' ? row.strategy : row.account_id ?? 'SHARED_PAPER',
      account_ids: [row.account_id ?? 'SHARED_PAPER'],
      profiles: { [row.profile]: row },
      profile_account_refs: { [row.profile]: { account_scope: row.account_scope ?? 'MAIN', account_id: row.account_id ?? 'SHARED_PAPER' } },
      rows: [row], raw_result_row_count: 1,
      base_result_row_count: row.profile === 'BASE' ? 1 : 0,
      stress_result_row_count: row.profile === 'STRESS' ? 1 : 0,
      partial_exit_row_count: 0,
    }],
    rows: [row], raw_result_row_count: 1,
    base_result_row_count: row.profile === 'BASE' ? 1 : 0,
    stress_result_row_count: row.profile === 'STRESS' ? 1 : 0,
    partial_exit_row_count: 0,
    replay_available: row.replay_available !== false,
  }] : []
  return {
    schema_version: 1,
    opportunities,
    counts: {
      unique_opportunities: opportunities.length,
      raw_result_rows: opportunities.length,
      base_result_rows: row?.profile === 'BASE' ? 1 : 0,
      stress_result_rows: row?.profile === 'STRESS' ? 1 : 0,
    },
    grouping_status: 'PROVEN', source_status: 'COMPLETE',
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
}

test('keeps partial BASE and STRESS ledger rows in one completed opportunity', async () => {
  const data = dashboardFixture()
  const template: HistoryRow = {
    run_id: data.status.run_id, trade_id: 'trade-v6-template', opportunity_id: 'opportunity-v6',
    symbol: 'BTCUSDT', strategy: strategies[1].strategy_id, side: 'LONG',
    entry: '100', exit: '101', entry_ts_ms: 1_000, exit_ts_ms: 2_000,
    initial_stop: '99', take_profit: '102', quantity: '1', exit_reason: 'TAKE_PROFIT',
    gross_pnl: '1.5', fees: '0.15', slippage: '0.1', net_pnl: '1.25',
    holding_ms: 1_000, holding_seconds: 1, profile: 'BASE', sample_type: 'OFFLINE_FIXTURE',
    account_scope: 'LEAGUE', account_id: `${strategies[1].strategy_id}:BASE`,
    strategy_version: data.history_scope.strategy_version, replay_available: true,
  }
  const base = { ...template, trade_id: 'trade-v6-base', opportunity_id: 'opportunity-v6', account_scope: 'LEAGUE' as const, profile: 'BASE', quantity: '0.4', net_pnl: '0.75' }
  const basePartial = { ...base, trade_id: 'trade-v6-base-partial', quantity: '0.6', exit_ts_ms: 2_500, holding_ms: 1_500, holding_seconds: 1.5, net_pnl: '0.5' }
  const stress = { ...template, trade_id: 'trade-v6-stress', opportunity_id: 'opportunity-v6', account_scope: 'LEAGUE' as const, account_id: `${strategies[1].strategy_id}:STRESS`, profile: 'STRESS', quantity: '0.4', net_pnl: '0.45' }
  const stressPartial = { ...stress, trade_id: 'trade-v6-stress-partial', quantity: '0.6', exit_ts_ms: 2_500, holding_ms: 1_500, holding_seconds: 1.5, net_pnl: '0.3' }
  const grouped: TradesResponse = {
    schema_version: 1,
    opportunities: [{
      key: { run_id: template.run_id, strategy_id: template.strategy, strategy_version: template.strategy_version ?? 'V1', opportunity_id: 'opportunity-v6', symbol: template.symbol, side: template.side },
      family_id: 'BREAKOUT_RUNNER', family_label_ko: '돌파·큰 추세', variant_label_ko: '현재 variant',
      entry_ts_ms: template.entry_ts_ms, exit_ts_ms: template.exit_ts_ms,
      profiles: { BASE: [base, basePartial], STRESS: [stress, stressPartial] }, rows: [base, basePartial, stress, stressPartial],
      account_groups: [{
        account_scope: 'LEAGUE', account_group_id: template.strategy,
        account_ids: [`${template.strategy}:BASE`, `${template.strategy}:STRESS`],
        profiles: { BASE: [base, basePartial], STRESS: [stress, stressPartial] },
        profile_account_refs: {
          BASE: { account_scope: 'LEAGUE', account_id: `${template.strategy}:BASE` },
          STRESS: { account_scope: 'LEAGUE', account_id: `${template.strategy}:STRESS` },
        },
        rows: [base, basePartial, stress, stressPartial], raw_result_row_count: 4,
        base_result_row_count: 2, stress_result_row_count: 2, partial_exit_row_count: 2,
      }],
      raw_result_row_count: 4, base_result_row_count: 2, stress_result_row_count: 2,
      partial_exit_row_count: 2, replay_available: true,
    }],
    counts: { unique_opportunities: 1, raw_result_rows: 4, base_result_rows: 2, stress_result_rows: 2, unresolved_result_rows: 1 },
    grouping_status: 'NOT_PROVEN',
    source_status: 'COMPLETE',
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void input
    void init
    return new Response(JSON.stringify(grouped), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<TradesPage data={data} />)
  await waitFor(() => expect(screen.getByText(/완료 1회 · 원장 4행/)).toBeInTheDocument())
  fireEvent.click(screen.getByRole('tab', { name: '완료' }))

  expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1)
  expect(screen.getByText(/비용 2개 비교/)).toBeInTheDocument()
  expect(screen.getByText('+1.25 USDT')).toBeInTheDocument()
  expect(screen.getByText('+0.75 USDT')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/trades'), expect.any(Object))
  expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/history'))).toBe(false)
  expect(screen.getByText(/1행은 진입기회 수에서 제외/)).toBeInTheDocument()
  fireEvent.click(screen.getByText('기록 범위 바꾸기'))
  fireEvent.change(screen.getByLabelText('Run 범위'), { target: { value: 'ALL' } })
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    expect.stringMatching(/^\/api\/trades\?.*run_scope=ALL/),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  ))
  expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/history'))).toBe(false)
  expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1)
})

test('polls completed trades without overlapping and refreshes a closure in the same Run', async () => {
  vi.useFakeTimers()
  const data = dashboardFixture()
  const completed: HistoryRow = {
    run_id: data.status.run_id, trade_id: 'trade-polled', opportunity_id: 'opportunity-polled',
    strategy: strategies[1].strategy_id, strategy_version: data.history_scope.strategy_version,
    symbol: 'ETHUSDT', side: 'LONG', entry: '100', exit: '101', entry_ts_ms: 1_000,
    exit_ts_ms: 2_000, initial_stop: '99', take_profit: '102', quantity: '1',
    exit_reason: 'TAKE_PROFIT', gross_pnl: '1', fees: '0.1', slippage: '0.1', net_pnl: '0.8',
    holding_ms: 1_000, holding_seconds: 1, profile: 'BASE', sample_type: 'LIVE_PUBLIC',
    account_scope: 'MAIN', account_id: 'SHARED_PAPER', replay_available: true,
  }
  let resolveFirst!: (value: Response) => void
  const firstResponse = new Promise<Response>((resolve) => { resolveFirst = resolve })
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => firstResponse)
    .mockResolvedValue(response(groupedTradesResponse(completed)))
  vi.stubGlobal('fetch', fetchMock)

  const view = render(<TradesPage data={data} />)
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  resolveFirst(response(groupedTradesResponse()))
  await vi.waitFor(() => expect(screen.getByText(/5초마다 확인/)).toBeInTheDocument())
  await vi.advanceTimersByTimeAsync(5_000)
  await vi.waitFor(() => expect(screen.getByText(/완료 1회 · 원장 1행/)).toBeInTheDocument())
  expect(fetchMock).toHaveBeenCalledTimes(2)
  const latestSignal = (fetchMock.mock.calls.at(-1)?.[1] as RequestInit | undefined)?.signal as AbortSignal
  view.unmount()
  expect(latestSignal.aborted).toBe(true)
})

test('shows a truthful completed-trade error instead of a zero-row dashboard fallback', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
  render(<TradesPage data={dashboardFixture()} />)

  expect(await screen.findByText('완료 거래 갱신을 확인해야 합니다.')).toBeInTheDocument()
  expect(screen.queryByText(/완료 원장 0행/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: '완료' }))
  expect(screen.getByRole('alert')).toHaveTextContent('완료 거래를 불러오지 못했습니다')
})

test('keeps shared and League account quantities and PnL in separate result rows', async () => {
  const data = dashboardFixture()
  const strategyId = strategies[1].strategy_id
  const common: HistoryRow = {
    run_id: data.status.run_id, trade_id: 'trade-account-template',
    opportunity_id: 'opportunity-account-isolation', symbol: 'BTCUSDT',
    strategy: strategyId, side: 'LONG', entry: '100', exit: '101',
    entry_ts_ms: 1_000, exit_ts_ms: 2_000, initial_stop: '99', take_profit: '102',
    quantity: '1', exit_reason: 'TAKE_PROFIT', gross_pnl: '1.2', fees: '0.1',
    slippage: '0.1', net_pnl: '1', holding_ms: 1_000, holding_seconds: 1,
    profile: 'BASE', sample_type: 'LIVE_PUBLIC', strategy_version: data.history_scope.strategy_version,
    replay_available: true,
  }
  const shared = {
    ...common, trade_id: 'trade-shared-base', account_scope: 'MAIN' as const,
    account_id: 'SHARED_PAPER', quantity: '10', gross_pnl: '102', net_pnl: '100',
  }
  const leagueBase = {
    ...common, trade_id: 'trade-league-base', account_scope: 'LEAGUE' as const,
    account_id: `${strategyId}:BASE`, quantity: '1', net_pnl: '1',
  }
  const leagueStress = {
    ...common, trade_id: 'trade-league-stress', account_scope: 'LEAGUE' as const,
    account_id: `${strategyId}:STRESS`, profile: 'STRESS', quantity: '2', net_pnl: '0.5',
  }
  const grouped: TradesResponse = {
    schema_version: 1,
    opportunities: [{
      key: {
        run_id: common.run_id, strategy_id: strategyId,
        strategy_version: common.strategy_version ?? 'V1',
        opportunity_id: common.opportunity_id ?? 'missing', symbol: common.symbol, side: common.side,
      },
      family_id: 'BREAKOUT_RUNNER', family_label_ko: '돌파·큰 추세',
      variant_label_ko: '현재 variant', entry_ts_ms: 1_000, exit_ts_ms: 2_000,
      profiles: { BASE: shared },
      profile_account_refs: {
        BASE: { account_scope: 'MAIN', account_id: 'SHARED_PAPER' },
      },
      account_groups: [{
        account_scope: 'MAIN', account_group_id: 'SHARED_PAPER',
        account_ids: ['SHARED_PAPER'], profiles: { BASE: shared },
        profile_account_refs: {
          BASE: { account_scope: 'MAIN', account_id: 'SHARED_PAPER' },
        },
        rows: [shared], raw_result_row_count: 1, base_result_row_count: 1,
        stress_result_row_count: 0, partial_exit_row_count: 0,
      }, {
        account_scope: 'LEAGUE', account_group_id: strategyId,
        account_ids: [`${strategyId}:BASE`, `${strategyId}:STRESS`],
        profiles: { BASE: leagueBase, STRESS: leagueStress },
        profile_account_refs: {
          BASE: { account_scope: 'LEAGUE', account_id: `${strategyId}:BASE` },
          STRESS: { account_scope: 'LEAGUE', account_id: `${strategyId}:STRESS` },
        },
        rows: [leagueBase, leagueStress], raw_result_row_count: 2,
        base_result_row_count: 1, stress_result_row_count: 1, partial_exit_row_count: 0,
      }],
      rows: [shared, leagueBase, leagueStress], raw_result_row_count: 3,
      base_result_row_count: 2, stress_result_row_count: 1,
      partial_exit_row_count: 0, replay_available: true,
    }],
    counts: {
      unique_opportunities: 1, raw_result_rows: 3,
      base_result_rows: 2, stress_result_rows: 1,
    },
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(grouped), {
    status: 200, headers: { 'content-type': 'application/json' },
  })))

  render(<TradesPage data={data} />)
  await waitFor(() => expect(screen.getByText(/완료 1회 · 원장 3행/)).toBeInTheDocument())
  fireEvent.click(screen.getByRole('tab', { name: '완료' }))

  const resultRows = [...document.querySelectorAll<HTMLTableRowElement>('.history-table tbody tr')]
  expect(resultRows).toHaveLength(1)
  const resultRow = resultRows[0]
  expect(within(resultRow).getByText('+100 USDT')).toBeInTheDocument()
  expect(within(resultRow).queryByText('+101 USDT')).not.toBeInTheDocument()
  expect(within(resultRow).getByText('+1 USDT')).toBeInTheDocument()
  expect(within(resultRow).getByText('+0.5 USDT')).toBeInTheDocument()
  expect(within(resultRow).getByText(/같은 진입기회/)).toBeInTheDocument()

  fireEvent.click(within(resultRow).getByRole('button', { name: '결과 비교' }))
  fireEvent.click(screen.getByText('기술 정보'))
  expect(screen.getByText('계좌 코드').parentElement).toHaveTextContent('SHARED_PAPER')
  expect(screen.getByText('수량').parentElement).toHaveTextContent('10')
})

test('loads a selected strategy family detail on demand and keeps the registry row usable', async () => {
  const strategy = {
    ...strategies[1],
    family_id: 'BREAKOUT_RUNNER',
    family_label_ko: '돌파·큰 추세',
    variant_label_ko: '30분 돌파 후 재확인 V2',
    is_current_variant: true,
    user_visible_by_default: true,
  }
  const detail = {
    family_id: 'BREAKOUT_RUNNER', label_ko: '돌파·큰 추세', category_ko: '방향성 진입',
    description_ko: 'family 상세 설명입니다.', current_variant_id: strategy.strategy_id,
    variants: [{
      strategy_id: strategy.strategy_id,
      variant_label_ko: strategy.variant_label_ko,
      is_current_variant: true,
      runtime_state: strategy,
      setting: { research_enabled: true, settings_revision: 7, mode: 'SHADOW' },
      research_sources: [{
        source_id: 'SRC-TSMOM-2012',
        title: 'Time Series Momentum',
        publisher: 'Journal of Financial Economics',
        date: '2012',
        url: 'https://doi.org/10.1016/j.jfineco.2011.11.003',
        idea_used: '시계열 모멘텀의 방향성 검증',
        our_modification: '공개시장 PAPER 비용 게이트와 결합',
        metadata_status: 'REGISTERED',
      }],
    }, {
      strategy_id: 'BREAKOUT_RETEST_30M_V1',
      variant_label_ko: '30분 돌파 V1',
      is_current_variant: false,
      role: 'LEGACY',
      setting: { research_enabled: false, settings_revision: 3, mode: 'OFF', lifecycle: 'RETIRED' },
    }],
    offline_challengers: [{ strategy_id: 'BREAKOUT_RETEST_30M_V3', state: 'PREREGISTERED_NOT_EXECUTED' }],
  }
  const conditions = {
    schema_version: 1, family_id: 'BREAKOUT_RUNNER', strategy_id: strategy.strategy_id,
    symbol: 'BTCUSDT', setup_state: 'BLOCKED', passed: 1, total: 2,
    top_blockers: ['RAW_SETUP_REASON_CODE'],
    conditions: [{
      condition_id: 'VOLUME_CONFIRMATION', label_ko: '거래량 확인', threshold_ko: '0.65 이상',
      current_value: '0.72', status: 'PASSED', reason_ko: '거래량 기준을 충족했습니다.',
    }, {
      condition_id: 'SNAPSHOT_HASH', label_ko: '실측 해시', threshold_ko: '기술 무결성 확인',
      current_value: 'a'.repeat(64), status: 'BLOCKED', reason_ko: '현재 호가 흐름의 지속 확인이 필요합니다.',
    }],
    sides: [],
    pending_count: 1,
    open_count: 2,
    execution: {
      entry: '101.5',
      initial_stop: '99.0',
      TP1: '104.0',
      TP2: '108.0',
      trailing_activation: '103.5',
      current_trail: '102.8',
      remaining_quantity: '0.6',
      expiry: '2026-09-01T00:00:00Z',
    },
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init
    const path = String(input)
    if (path === '/api/strategy-families/ORDERFLOW_CONFIRMATION/conditions') return response(emptyConditions('ORDERFLOW_CONFIRMATION'))
    if (path === '/api/strategy-families/BREAKOUT_RUNNER/conditions') return response(conditions)
    return response(detail)
  })
  const onSelectFamily = vi.fn()
  vi.stubGlobal('fetch', fetchMock)

  const drawerProps = {
    strategies: [strategy],
    leagueAccounts: leagueAccounts.filter((account) => account.strategy_id === strategy.strategy_id),
    onSelectFamily,
    onConfigure: vi.fn(async () => undefined),
  }
  const view = render(<StrategiesPage {...drawerProps} researchDetails={false} controlsEnabled />)
  const row = document.querySelector(`[data-strategy-id="${strategy.strategy_id}"]`)
  if (!(row instanceof HTMLElement)) throw new Error('strategy row missing')
  const openButton = within(row).getByRole('button', { name: '자세히·설정' })
  openButton.focus()
  fireEvent.click(openButton)
  expect(onSelectFamily).toHaveBeenCalledWith('BREAKOUT_RUNNER')
  const dialog = screen.getByRole('dialog', { name: '전략 상세 정보' })
  expect(within(dialog).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
    '지금 상태',
    '진입조건',
    '청산',
    '성과',
    '출처',
    '이전 버전',
  ])
  expect(within(dialog).getByRole('button', { name: '전략 상세 정보 닫기' })).toHaveFocus()

  expect(await screen.findByText('family 상세 설명입니다.')).toBeInTheDocument()
  expect(screen.queryByText('1개 · 실행 전 연구')).not.toBeInTheDocument()
  expect(screen.getByText('진입 조건 대기')).toBeInTheDocument()
  expect(screen.getByText('1/2')).toBeInTheDocument()
  expect(screen.getAllByText('현재 호가 흐름의 지속 확인이 필요합니다.')).toHaveLength(1)
  expect(screen.getByText('1건')).toBeInTheDocument()
  expect(screen.getByText('2건')).toBeInTheDocument()

  const statusTab = within(dialog).getByRole('tab', { name: '지금 상태' })
  statusTab.focus()
  fireEvent.keyDown(statusTab, { key: 'ArrowRight' })
  const conditionsTab = within(dialog).getByRole('tab', { name: '진입조건' })
  expect(conditionsTab).toHaveFocus()
  expect(conditionsTab).toHaveAttribute('aria-selected', 'true')
  expect(within(dialog).getAllByRole('tab').filter((tab) => tab.tabIndex === 0)).toEqual([conditionsTab])
  const conditionsPanel = within(dialog).getByRole('tabpanel')
  expect(conditionsTab).toHaveAttribute('aria-controls', conditionsPanel.id)
  expect(conditionsPanel).toHaveAttribute('aria-labelledby', conditionsTab.id)
  expect(await screen.findByRole('table', { name: '선택 전략의 진입 조건 실측' })).toBeInTheDocument()
  expect(screen.getByText('0.65 이상')).toBeInTheDocument()
  expect(screen.getByText('0.72')).toBeInTheDocument()
  expect(screen.getByText('기술 값 숨김')).toBeInTheDocument()
  expect(screen.getAllByText('현재 호가 흐름의 지속 확인이 필요합니다.')).toHaveLength(1)

  fireEvent.click(within(dialog).getByRole('tab', { name: '청산' }))
  expect(await screen.findByRole('region', { name: '선택 전략의 청산 정보' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '선택 전략의 청산 정보' })).toHaveTextContent('101.5')
  expect(screen.getByRole('region', { name: '선택 전략의 청산 정보' })).toHaveTextContent('99.0')
  expect(screen.getByRole('region', { name: '선택 전략의 청산 정보' })).toHaveTextContent('104.0')
  expect(screen.getByRole('region', { name: '선택 전략의 청산 정보' })).toHaveTextContent('108.0')

  expect(screen.queryByText('30분 돌파 V1')).not.toBeInTheDocument()
  expect(screen.queryByText('Time Series Momentum')).not.toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('tab', { name: '이전 버전' }))
  expect(await screen.findByText('30분 돌파 V1')).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('tab', { name: '출처' }))
  expect(await screen.findByText('Time Series Momentum')).toBeInTheDocument()
  const sourceLink = screen.getByRole('link', { name: '원문 새 창에서 열기' })
  expect(sourceLink).toHaveAttribute('target', '_blank')
  expect(sourceLink).toHaveAttribute('rel', 'noopener noreferrer')

  fireEvent.click(within(dialog).getByRole('tab', { name: '성과' }))
  expect(screen.getAllByText('고유 진입기회')).toHaveLength(1)
  expect(screen.getAllByText('승률 · Wilson 하한')).toHaveLength(1)
  expect(screen.getAllByText('Profit Factor')).toHaveLength(1)
  expect(screen.getAllByText('현재 버전 순손익')).toHaveLength(1)
  expect(screen.getAllByText('Runner 기여')).toHaveLength(1)
  fireEvent.click(within(dialog).getByRole('button', { name: '보수 비용' }))
  expect(within(dialog).getByRole('heading', { name: '보수 비용 가상계좌' })).toBeInTheDocument()

  fireEvent.click(within(dialog).getByRole('tab', { name: '지금 상태' }))
  view.rerender(<StrategiesPage {...drawerProps} researchDetails controlsEnabled />)
  expect(screen.getByText('1개 · 실행 전 연구')).toBeInTheDocument()
  expect(screen.queryByText('RAW_SETUP_REASON_CODE')).not.toBeInTheDocument()
  expect(screen.queryByText('a'.repeat(64))).not.toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/strategy-families/BREAKOUT_RUNNER', expect.any(Object))
  expect(fetchMock).toHaveBeenCalledWith('/api/strategy-families/BREAKOUT_RUNNER/conditions', expect.any(Object))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onSelectFamily).toHaveBeenLastCalledWith(null)
  expect(openButton).toHaveFocus()
})

test('refreshes selected strategy conditions every five seconds while the drawer stays open', async () => {
  vi.useFakeTimers()
  const strategy = {
    ...strategies[1],
    family_id: 'BREAKOUT_RUNNER',
    family_label_ko: '돌파·큰 추세',
    is_current_variant: true,
    user_visible_by_default: true,
  }
  const detail = {
    family_id: 'BREAKOUT_RUNNER', label_ko: '돌파·큰 추세', category_ko: '방향성 진입',
    description_ko: 'family 상세', current_variant_id: strategy.strategy_id,
    variants: [{
      strategy_id: strategy.strategy_id, is_current_variant: true,
      runtime_state: strategy,
      setting: { research_enabled: true, settings_revision: 1, mode: 'SHADOW' },
    }],
    offline_challengers: [],
  }
  let familyConditionCalls = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/api/strategy-families/ORDERFLOW_CONFIRMATION/conditions') {
      return response(emptyConditions('ORDERFLOW_CONFIRMATION'))
    }
    if (path === '/api/strategy-families/BREAKOUT_RUNNER/conditions') {
      familyConditionCalls += 1
      return response({
        ...emptyConditions('BREAKOUT_RUNNER'),
        setup_state: familyConditionCalls > 1 ? 'QUALIFIED' : 'BLOCKED',
        passed: familyConditionCalls > 1 ? 1 : 0,
        total: 1,
        conditions: [{
          condition_id: 'REFRESHED', label_ko: '실시간 조건', threshold_ko: '충족',
          current_value: familyConditionCalls > 1 ? '충족' : '대기',
          status: familyConditionCalls > 1 ? 'PASSED' : 'WAITING',
          reason_ko: familyConditionCalls > 1 ? '실시간 조건을 충족했습니다.' : '실시간 조건을 확인하고 있습니다.',
        }],
      })
    }
    return response(detail)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<StrategiesPage strategies={[strategy]} leagueAccounts={[]} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  const row = document.querySelector(`[data-strategy-id="${strategy.strategy_id}"]`)
  if (!(row instanceof HTMLElement)) throw new Error('strategy row missing')
  fireEvent.click(within(row).getByRole('button', { name: '자세히·설정' }))
  await vi.waitFor(() => expect(screen.getByText('0/1')).toBeInTheDocument())
  await vi.advanceTimersByTimeAsync(5_000)
  await vi.waitFor(() => expect(screen.getByText('1/1')).toBeInTheDocument())
  expect(familyConditionCalls).toBe(2)
  expect(screen.getByText(/5초마다 자동 확인/)).toBeInTheDocument()
})

test('changes current-family research through CAS and performs a real undo with the returned revision', async () => {
  const strategy = {
    ...strategies[1],
    family_id: 'BREAKOUT_RUNNER',
    family_label_ko: '돌파·큰 추세',
    is_current_variant: true,
    user_visible_by_default: true,
    settings_revision: 7,
  }
  let revision = 7
  let researchEnabled = true
  const detail = () => ({
    family_id: 'BREAKOUT_RUNNER', label_ko: '돌파·큰 추세', category_ko: '방향성 진입',
    description_ko: 'family 상세 설명입니다.', current_variant_id: strategy.strategy_id,
    variants: [{
      strategy_id: strategy.strategy_id,
      is_current_variant: true,
      setting: {
        research_enabled: researchEnabled,
        settings_revision: revision,
        mode: (researchEnabled ? 'SHADOW' : 'OFF') as 'SHADOW' | 'OFF',
      },
      runtime_state: {
        ...strategy,
        mode: (researchEnabled ? 'SHADOW' : 'OFF') as 'SHADOW' | 'OFF',
        settings_revision: revision,
      },
    }],
    offline_challengers: [],
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path.endsWith('/conditions')) return response(emptyConditions(path.includes('ORDERFLOW') ? 'ORDERFLOW_CONFIRMATION' : 'BREAKOUT_RUNNER'))
    return response(detail())
  })
  const onConfigure = vi.fn(async () => undefined)
  const onConfigureFamilyResearch = vi.fn(async (
    familyId: string,
    configuration: { research_enabled: boolean; expected_revision: number; reason: string },
  ) => {
    expect(familyId).toBe('BREAKOUT_RUNNER')
    expect(configuration.expected_revision).toBe(revision)
    researchEnabled = configuration.research_enabled
    revision += 1
    return detail()
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(<StrategiesPage strategies={[strategy]} leagueAccounts={[]} researchDetails controlsEnabled onConfigure={onConfigure} onConfigureFamilyResearch={onConfigureFamilyResearch} />)
  const row = document.querySelector(`[data-strategy-id="${strategy.strategy_id}"]`)
  if (!(row instanceof HTMLElement)) throw new Error('strategy row missing')
  fireEvent.click(within(row).getByRole('button', { name: '자세히·설정' }))
  const offButton = await screen.findByRole('button', { name: `${strategy.short_name} 모의평가 끄기` })

  fireEvent.click(offButton)

  await waitFor(() => expect(onConfigureFamilyResearch).toHaveBeenCalledTimes(1))
  expect(onConfigureFamilyResearch.mock.calls[0]).toEqual(['BREAKOUT_RUNNER', {
    research_enabled: false,
    expected_revision: 7,
    reason: 'USER_STRATEGY_CENTER_RESEARCH_OFF',
  }])
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/research-enabled'))).toBe(false)
  expect(await screen.findByRole('button', { name: `${strategy.short_name} 모의평가 끄기` })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText('rev 8', { exact: false })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '실행 취소' }))

  await waitFor(() => expect(onConfigureFamilyResearch).toHaveBeenCalledTimes(2))
  expect(onConfigureFamilyResearch.mock.calls[1]).toEqual(['BREAKOUT_RUNNER', {
    research_enabled: true,
    expected_revision: 8,
    reason: 'USER_STRATEGY_CENTER_RESEARCH_UNDO',
  }])
  expect(await screen.findByRole('button', { name: `${strategy.short_name} 모의평가 켜기` })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText('rev 9', { exact: false })).toBeInTheDocument()
  expect(onConfigure).not.toHaveBeenCalled()
})

test('keeps family research controls locked and sends no mutation when PAPER safety is unverified', async () => {
  const strategy = {
    ...strategies[1],
    family_id: 'BREAKOUT_RUNNER',
    family_label_ko: '돌파·큰 추세',
    is_current_variant: true,
    user_visible_by_default: true,
    settings_revision: 7,
  }
  const detail = {
    family_id: 'BREAKOUT_RUNNER',
    label_ko: '돌파·큰 추세',
    current_variant_id: strategy.strategy_id,
    variants: [{
      strategy_id: strategy.strategy_id,
      is_current_variant: true,
      setting: { research_enabled: true, settings_revision: 7, mode: 'SHADOW' as const },
      runtime_state: strategy,
    }],
    offline_challengers: [],
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => (
    response(String(input).endsWith('/conditions') ? emptyConditions('BREAKOUT_RUNNER') : detail)
  ))
  const onConfigureFamilyResearch = vi.fn(async () => detail)
  vi.stubGlobal('fetch', fetchMock)

  render(<StrategiesPage
    strategies={[strategy]}
    leagueAccounts={[]}
    controlsEnabled={false}
    onConfigure={vi.fn(async () => undefined)}
    onConfigureFamilyResearch={onConfigureFamilyResearch}
  />)
  const row = document.querySelector(`[data-strategy-id="${strategy.strategy_id}"]`)
  if (!(row instanceof HTMLElement)) throw new Error('strategy row missing')
  fireEvent.click(within(row).getByRole('button', { name: '자세히·설정' }))
  const offButton = await screen.findByRole('button', { name: `${strategy.short_name} 모의평가 끄기` })

  expect(offButton).toBeDisabled()
  fireEvent.click(offButton)
  expect(onConfigureFamilyResearch).not.toHaveBeenCalled()
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/research-enabled'))).toBe(false)
})

test('shows the ORDERFLOW research filter truth and toggles it with the visible CAS revision', async () => {
  const affected = strategies.filter((strategy) => [
    'TREND_PULLBACK_RECLAIM_15M_V2',
    'BREAKOUT_RETEST_30M_V2',
  ].includes(strategy.strategy_id)).map((strategy) => ({
    ...strategy,
    family_label_ko: strategy.strategy_id.startsWith('TREND') ? '추세 눌림·재합류' : '돌파·큰 추세',
  }))
  let enabled = false
  let revision = 4
  const orderflowPayload = () => ({
    ...emptyConditions('ORDERFLOW_CONFIRMATION'),
    strategy_id: 'ORDERFLOW_CONFIRMATION_FILTER_V2',
    setup_state: enabled ? 'WAITING_DATA' : 'FILTER_OFF',
    filter: {
      filter_id: 'ORDERFLOW_CONFIRMATION_FILTER_V2',
      enabled,
      revision,
      change_reason: 'RAW_REASON_MUST_STAY_HIDDEN',
      affected_strategy_ids: affected.map((strategy) => strategy.strategy_id),
      latest: [{
        symbol: 'BTCUSDT', side: 'LONG', score: '0.72', data_health: 'HEALTHY',
        reason_codes: ['RAW_FILTER_REASON'], creates_candidate_plan: false,
      }],
      uplift_status: 'NOT_PROVEN_NO_PAIRED_FILTER_SAMPLE',
      paper_only: true,
    },
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    void input
    return response(orderflowPayload())
  })
  const onConfigureFamilyResearch = vi.fn(async (
    familyId: string,
    configuration: { research_enabled: boolean; expected_revision: number; reason: string },
  ) => {
    expect(familyId).toBe('ORDERFLOW_CONFIRMATION')
    expect(configuration.expected_revision).toBe(revision)
    enabled = configuration.research_enabled
    revision += 1
    return {
      family_id: 'ORDERFLOW_CONFIRMATION',
      variants: [{
        strategy_id: 'ORDERFLOW_CONFIRMATION_FILTER_V2',
        is_current_variant: true,
        setting: { research_enabled: enabled, settings_revision: revision },
      }],
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(<StrategiesPage strategies={affected} leagueAccounts={[]} controlsEnabled onConfigure={vi.fn(async () => undefined)} onConfigureFamilyResearch={onConfigureFamilyResearch} />)

  expect(await screen.findByText('OFF')).toBeInTheDocument()
  expect(screen.getByText('0.72 / 1.00')).toBeInTheDocument()
  expect(screen.getByText('추세 눌림·재합류 · 돌파·큰 추세')).toBeInTheDocument()
  expect(screen.getByText('ON/OFF 비교 표본을 모으는 중')).toBeInTheDocument()
  expect(screen.getByText('정상')).toBeInTheDocument()
  expect(screen.getByText('자체 CandidatePlan·계좌·거래를 만들지 않는 PAPER 확인 필터입니다.')).toBeInTheDocument()
  expect(screen.queryByText(/실제 주문 0/)).not.toBeInTheDocument()
  expect(screen.queryByText('NOT_PROVEN_NO_PAIRED_FILTER_SAMPLE')).not.toBeInTheDocument()
  expect(screen.queryByText('RAW_REASON_MUST_STAY_HIDDEN')).not.toBeInTheDocument()
  expect(screen.queryByText('RAW_FILTER_REASON')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '주문흐름 확인 필터 켜기' }))

  await waitFor(() => expect(onConfigureFamilyResearch).toHaveBeenCalledTimes(1))
  expect(onConfigureFamilyResearch.mock.calls[0]).toEqual(['ORDERFLOW_CONFIRMATION', {
    research_enabled: true,
    expected_revision: 4,
    reason: 'USER_STRATEGY_CENTER_ORDERFLOW_FILTER',
  }])
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/research-enabled'))).toBe(false)
  expect(await screen.findByRole('button', { name: '주문흐름 확인 필터 끄기' })).toHaveAttribute('aria-pressed', 'true')
})

test('makes ORDERFLOW loading, error and missing runtime states explicit', async () => {
  let attempts = 0
  vi.stubGlobal('fetch', vi.fn(async () => {
    attempts += 1
    if (attempts === 1) return response({ detail: { error_message_ko: '주문흐름 상태를 읽지 못했습니다.' } }, 503)
    return response(emptyConditions('ORDERFLOW_CONFIRMATION'))
  }))

  render(<StrategiesPage strategies={strategies.slice(1, 2)} leagueAccounts={[]} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  expect(screen.getByText('주문흐름 필터 상태를 불러오는 중입니다.')).toBeInTheDocument()
  expect(await screen.findByRole('alert')).toHaveTextContent('주문흐름 상태를 읽지 못했습니다.')
  fireEvent.click(screen.getByRole('button', { name: '필터 상태 다시 불러오기' }))
  expect(await screen.findByText('주문흐름 필터 상태가 아직 없습니다.')).toBeInTheDocument()
  expect(screen.getByText(/ON\/OFF로 표시하거나 진입 효과를 단정하지 않습니다/)).toBeInTheDocument()
})

test('filters the seven family categories and ranks only eligible strategies with 30 unique opportunities', async () => {
  const makeRow = (
    sourceIndex: number,
    familyId: string,
    uniqueSamples: number,
    rankingEligible: boolean,
    role = 'ENTRY',
  ) => {
    const source = strategies[sourceIndex]
    return {
      ...source,
      family_id: familyId,
      family_label_ko: `${familyId} family`,
      role,
      is_current_variant: true,
      user_visible_by_default: true,
      final_ranking_eligible: rankingEligible,
      lifecycle: 'SHADOW' as const,
      performance: {
        BASE: { ...source.performance.BASE, sample_size: uniqueSamples, unique_opportunity_count: uniqueSamples },
        STRESS: { ...source.performance.STRESS, sample_size: uniqueSamples, unique_opportunity_count: uniqueSamples },
      },
    }
  }
  const rows = [
    makeRow(11, 'TREND_PULLBACK', 31, true),
    makeRow(13, 'BREAKOUT_RUNNER', 29, true),
    makeRow(2, 'EXHAUSTION_REVERSION', 40, false),
    makeRow(6, 'MARKET_NEUTRAL', 35, true, 'MARKET_NEUTRAL_MULTI_LEG'),
  ]
  vi.stubGlobal('fetch', vi.fn(async () => response(emptyConditions('ORDERFLOW_CONFIRMATION'))))

  render(<StrategiesPage strategies={rows} leagueAccounts={[]} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  const categoryTabs = screen.getByRole('tablist', { name: '전략 family 분류' })
  const visibleIds = () => [...document.querySelectorAll<HTMLTableRowElement>('.strategy-compact-table tbody tr')].map((row) => row.dataset.strategyId)

  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent('방향 진입 후보 ON')
  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent('진행 포지션')
  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent('30건 이상')
  expect(screen.getByRole('columnheader', { name: '모의평가' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'PF' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '주요 대기이유' })).toBeInTheDocument()
  expect(within(categoryTabs).getAllByRole('tab')).toHaveLength(7)
  expect(visibleIds()).toHaveLength(4)
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '추세' }))
  expect(visibleIds()).toEqual([rows[0].strategy_id])
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '돌파' }))
  expect(visibleIds()).toEqual([rows[1].strategy_id])
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '반전' }))
  expect(visibleIds()).toEqual([rows[2].strategy_id])
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '필터' }))
  expect(screen.getByRole('heading', { name: '주문흐름 확인 필터' })).toBeInTheDocument()
  expect(visibleIds()).toHaveLength(0)
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '시장중립' }))
  expect(visibleIds()).toEqual([rows[3].strategy_id])
  fireEvent.click(within(categoryTabs).getByRole('tab', { name: '순위' }))
  expect(new Set(visibleIds())).toEqual(new Set([rows[0].strategy_id, rows[3].strategy_id]))
  expect(visibleIds()).not.toContain(rows[1].strategy_id)
  expect(visibleIds()).not.toContain(rows[2].strategy_id)
})

test('shows catalog families without a runtime current variant as preparation states, not weak profit', async () => {
  const data = dashboardFixture()
  data.strategy_family_catalog = {
    schema_version: 1,
    families: [{
      family_id: 'POSITIONING_LIQUIDATION', label_ko: '포지셔닝·청산', category_ko: '필터', description_ko: '설명',
      display_order: 50, current_variant_id: null, variant_count: 0, availability_state: 'RESEARCH_PREPARATION',
      availability_label_ko: '연구 준비', availability_reason_ko: '실측 소스와 진입 계약을 준비 중입니다.', variants: [],
    }, {
      family_id: 'MARKET_REGIME_FILTERS', label_ko: '시장 레짐', category_ko: '필터', description_ko: '설명',
      display_order: 60, current_variant_id: null, variant_count: 0, availability_state: 'ROUTER_ONLY',
      availability_label_ko: '라우터 전용', availability_reason_ko: '단독 진입 전략이 아닙니다.', variants: [],
    }, {
      family_id: 'SESSION_PROFILE', label_ko: '세션 프로필', category_ko: '필터', description_ko: '설명',
      display_order: 70, current_variant_id: null, variant_count: 0, availability_state: 'RESEARCH_PREPARATION',
      availability_label_ko: '연구 준비', availability_reason_ko: '세션 별 실측 검증 준비 중입니다.', variants: [],
    }, {
      family_id: 'MARKET_NEUTRAL', label_ko: '시장중립', category_ko: '시장중립', description_ko: '설명',
      display_order: 80, current_variant_id: null, variant_count: 0, availability_state: 'ENGINE_VALIDATION_REQUIRED',
      availability_label_ko: '엔진 검증 필요', availability_reason_ko: '다중 leg PAPER 엔진 검증 전입니다.', variants: [],
    }],
    paper_only: true,
    real_orders_enabled: false,
    auth_required: false,
    private_api_enabled: false,
    api_key_enabled: false,
    wallet_enabled: false,
    runtime_ai_order_decision_enabled: false,
    funding_readiness: 'NOT_READY',
  }
  vi.stubGlobal('fetch', vi.fn(async () => response(emptyConditions('ORDERFLOW_CONFIRMATION'))))

  render(<StrategiesPage strategies={strategies.slice(11, 12)} leagueAccounts={[]} data={data} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  expect(screen.getAllByText('연구 준비')).toHaveLength(2)
  expect(screen.getByText('라우터 전용')).toBeInTheDocument()
  expect(screen.getByText('엔진 검증 필요')).toBeInTheDocument()
  expect(screen.getAllByText('성과 미달 판정이 아니라 runtime 역할·검증 준비 상태입니다.')).toHaveLength(4)
})

test('renders backend diagnostic labels and keeps macOS autostart explicitly not proven', () => {
  const data = dashboardFixture()
  data.settings_summary = {
    schema_version: 1,
    run: { run_id: data.status.run_id, mode: data.status.mode, venue: data.status.venue, new_run_preserves_history: true },
    safety: {
      paper_only: true, real_orders_enabled: false, auth_required: false, private_api_enabled: false,
      api_key_enabled: false, wallet_enabled: false, runtime_ai_order_decision_enabled: false,
      entry_state: data.paper_entry_intent.state, entry_revision: data.paper_entry_intent.revision, active_locks: [],
    },
    costs: data.risk.strategy_league,
    paper_research: paperResearchSettings(),
    storage: {},
    connection: { public_market_only: true },
    autostart: {
      state: 'NOT_PROVEN', paper_state_recovery_reported: true, launch_agent_verified: false, read_only: true,
      evidence_source: 'LAUNCH_AGENT_NOT_INSPECTED',
      evidence_ko: 'PAPER 상태 자동 복구는 macOS 로그인·재부팅 자동 시작의 증거가 아닙니다.',
    },
    local_preferences: { research_detail_default: false, research_detail_affects_execution: false },
    funding_readiness: 'NOT_READY',
  }
  data.diagnostics = {
    schema_version: 1,
    rows: [{
      key: 'automatic_recovery_enabled', label_ko: 'Backend PAPER 상태 자동 복구 계약', value: true,
      severity: 'INFO', user_visible: false, group: 'RUNTIME',
    }],
    raw: { automatic_recovery_enabled: true },
    paper_only: true,
    real_orders_enabled: false,
    auth_required: false,
    private_api_enabled: false,
    api_key_enabled: false,
    wallet_enabled: false,
    runtime_ai_order_decision_enabled: false,
    funding_readiness: 'NOT_READY',
  }

  const onResearchDetailsChange = vi.fn()
  const onConfigureLeverage = vi.fn(async () => undefined)
  const view = render(<SettingsPage data={data} connected lastUpdateMs={data.system.server_time_ms as number} researchDetails={false} onResearchDetailsChange={onResearchDetailsChange} onNewRun={vi.fn()} onConfigureLeverage={onConfigureLeverage} />)

  expect(screen.getByText('자동 시작 · 실행 상태')).toBeInTheDocument()
  expect(screen.getByText(/확인되지 않음/)).toBeInTheDocument()
  expect(screen.getByText(/PAPER 상태 자동 복구는 macOS/)).toBeInTheDocument()
  expect(screen.getByText('Backend PAPER 상태 자동 복구 계약')).toBeInTheDocument()
  expect(screen.getByText('원시 JSON 보기')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('combobox', { name: 'PAPER 레버리지' }), {
    target: { value: '100' },
  })
  fireEvent.click(screen.getByRole('button', { name: '선택 배수 적용' }))
  expect(onConfigureLeverage).toHaveBeenCalledWith(100, 0)
  expect(screen.getByRole('checkbox', { name: '연구 상세 표시' })).not.toBeChecked()
  fireEvent.click(screen.getByRole('checkbox', { name: '연구 상세 표시' }))
  expect(onResearchDetailsChange).toHaveBeenCalledWith(true)
  view.rerender(<SettingsPage data={data} connected lastUpdateMs={data.system.server_time_ms as number} researchDetails onResearchDetailsChange={onResearchDetailsChange} onNewRun={vi.fn()} onConfigureLeverage={vi.fn(async () => undefined)} />)
  expect(screen.getByRole('checkbox', { name: '연구 상세 표시' })).toBeChecked()
  expect(screen.getByText('전문가 진단').closest('details')).toHaveAttribute('open')
})
