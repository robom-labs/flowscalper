// Registry가 늘어나도 독립계좌와 쉬운 전략 설정이 동적으로 표시되는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LeaguePositionsPage } from '../src/pages/LeaguePositionsPage'
import { PerformancePage } from '../src/pages/PerformancePage'
import { StrategiesPage } from '../src/pages/StrategiesPage'
import { StrategySymbolPage } from '../src/pages/StrategySymbolPage'
import type { LeaguePosition } from '../src/types'
import { dashboardFixture, leagueAccounts, strategies } from './fixtures'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('shows eleven compact strategy rows, easy modes and BASE/STRESS account detail', () => {
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} />)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(11)
  expect(document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')).toHaveLength(11)
  expect(screen.queryByText('기록만 하기')).not.toBeInTheDocument()
  expect(screen.getByText('6개 감시 · 검증 중지 5개 · 문제 0개 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getAllByText('준비 중')).toHaveLength(6)
  expect(document.querySelectorAll('.strategy-monitor.off')).toHaveLength(5)

  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[0])
  expect(screen.getByRole('dialog', { name: '전략 상세 정보' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'BASE 가상계좌' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'STRESS 가상계좌' })).toBeInTheDocument()
  expect(screen.getAllByText(/이번 Run 현재자산/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText(/현재 전략 버전의 공개시장 PAPER 기준/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('과거 버전 제외')).toHaveLength(2)
  expect(screen.getByRole('heading', { name: '자동 평가 상태' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '전략 운용 계약' })).toBeInTheDocument()
  expect(screen.getByText('10초~3분')).toBeInTheDocument()
  expect(screen.getByText('TOP_OF_BOOK_BASE13_STRESS25_V1')).toBeInTheDocument()
  expect(screen.getByText('필요 데이터')).toBeInTheDocument()
  expect(screen.getByText('최소 준비')).toBeInTheDocument()
  expect(screen.getByText('진입 가설')).toBeInTheDocument()
  expect(screen.getByText('반증 조건')).toBeInTheDocument()
  expect(screen.getByText('근거 약화 종료')).toBeInTheDocument()
  expect(screen.getByText('위험예산')).toBeInTheDocument()
  expect(screen.getByText('대상 범위')).toBeInTheDocument()
  expect(screen.getByText('미래정보 방지')).toBeInTheDocument()
  expect(screen.getByText('연구 근거')).toBeInTheDocument()
  expect(screen.getByText('현재 상태 근거')).toBeInTheDocument()
  expect(screen.getByText('아직 검증 불충분')).toBeInTheDocument()
  expect(screen.getAllByText('표본 없음 · 0건')).toHaveLength(6)

  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[10])
  expect(screen.getByText('1시간~36시간')).toBeInTheDocument()
  expect(screen.getByText(/TP1 2.2R·40%/)).toBeInTheDocument()
})

test('shows lifecycle evidence and restores the prior revision without deleting history', async () => {
  const current = strategies[2]
  const revisionZero = {
    strategy_id: current.strategy_id,
    mode: 'SHADOW' as const,
    lifecycle: 'SHADOW' as const,
    long_enabled: true,
    short_enabled: true,
    settings_revision: 0,
    manual_lock: false,
    changed_by: 'MIGRATION' as const,
    change_reason: 'SAFE_DEFAULT',
    settings_updated_ts_ms: 0,
    policy_reactivation_locked: false,
    transition_id: `strategy-setting-run-fixture-${current.strategy_id}-rev-0`,
    previous_state: 'NONE',
    new_state: 'SHADOW|SHADOW|LONG=ON|SHORT=ON|MANUAL_LOCK=OFF',
    occurred_ts_ms: 0,
    cause: 'SAFE_DEFAULT',
    cause_code: 'SAFE_DEFAULT',
    description_ko: '전략 초기 설정을 적용했습니다.',
    actor: 'RECOVERY' as const,
    run_id: 'run-fixture',
    account_id: null,
    symbol: null,
    request_revision: 0,
    response_revision: 0,
    reversible: true,
  }
  const revisionOne = {
    ...revisionZero,
    short_enabled: false,
    settings_revision: 1,
    manual_lock: true,
    changed_by: 'USER_UI' as const,
    change_reason: 'USER_CONFIGURATION',
    settings_updated_ts_ms: 1_759_888_000_000,
    transition_id: `strategy-setting-run-fixture-${current.strategy_id}-rev-1`,
    previous_state: revisionZero.new_state,
    new_state: 'SHADOW|SHADOW|LONG=ON|SHORT=OFF|MANUAL_LOCK=ON',
    occurred_ts_ms: 1_759_888_000_000,
    cause: 'USER_CONFIGURATION',
    cause_code: 'USER_CONFIGURATION',
    description_ko: '전략 설정을 SHADOW 상태로 변경했습니다.',
    actor: 'USER_UI' as const,
    request_revision: 0,
    response_revision: 1,
  }
  const rows = strategies.map((strategy) => strategy.strategy_id === current.strategy_id ? {
    ...strategy,
    short_enabled: false,
    settings_revision: 1,
    manual_lock: true,
    governance: {
      ...strategy.governance,
      settings_revision: 1,
      manual_lock: true,
      change_history: [revisionZero, revisionOne],
    },
  } : strategy)
  const onRollback = vi.fn(async () => undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} onRollback={onRollback} />)

  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[2])
  const dialog = screen.getByRole('dialog', { name: '전략 상세 정보' })
  expect(within(dialog).getByText(/rev 0/)).toBeInTheDocument()
  expect(within(dialog).getByText(/rev 1/)).toBeInTheDocument()
  expect(within(dialog).getByText(/전략 설정을 SHADOW 상태로 변경/)).toBeInTheDocument()
  expect(within(dialog).getByText(/SHADOW\|SHADOW\|LONG=ON\|SHORT=ON.*USER_UI/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '직전 설정으로 복원' }))

  await waitFor(() => expect(onRollback).toHaveBeenCalledWith(
    current.strategy_id,
    0,
    1,
  ))
})

test('blocks policy-retired reactivation but keeps ordinary user OFF reversible', async () => {
  const userOffId = 'VWAP_EXHAUSTION_REVERSION_V1'
  const rows = strategies.map((strategy) => strategy.strategy_id === userOffId ? {
    ...strategy,
    mode: 'OFF' as const,
    lifecycle: 'RETIRED' as const,
    policy_reactivation_locked: false,
  } : strategy)
  const onConfigure = vi.fn(async () => undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} onConfigure={onConfigure} />)

  expect(screen.getByRole('button', { name: 'LSA 반전 공동·독립 모의 중' })).toBeDisabled()
  const reversible = screen.getByRole('button', { name: 'VWAP 소진 독립 모의 중' })
  expect(reversible).toBeEnabled()
  fireEvent.click(reversible)

  await waitFor(() => expect(onConfigure).toHaveBeenCalledWith(
    userOffId,
    expect.objectContaining({ mode: 'SHADOW', expected_revision: 0 }),
  ))
})

test('confirms mode changes and sends the visible settings revision', async () => {
  const onConfigure = vi.fn(async () => undefined)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} onConfigure={onConfigure} />)

  fireEvent.click(screen.getByRole('button', { name: 'CBR 돌파 공동·독립 모의 중' }))

  await waitFor(() => expect(onConfigure).toHaveBeenCalledWith(
    'CBR_CONTINUATION_V1',
    expect.objectContaining({ mode: 'ACTIVE', expected_revision: 0 }),
  ))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('진행 중 PAPER는 기존 계획대로 관리'))
})

test('distinguishes healthy condition waiting, open PAPER management and faults', () => {
  const rows = strategies.map((strategy, index) => ({
    ...strategy,
    evaluated_paths: index === 1 ? 24 : strategy.evaluated_paths,
    latest_status: index === 1 ? 'REJECTED' : strategy.latest_status,
    latest_reasons: index === 1 ? ['AGGRESSOR_FLOW_NOT_ALIGNED', 'QUEUE_ALIGNMENT_NOT_PERSISTENT'] : strategy.latest_reasons,
  }))
  const accounts = leagueAccounts.map((account) => ({ ...account }))
  const firstBase = accounts.find((account) => account.strategy_id === rows[1].strategy_id && account.profile === 'BASE')
  const secondStress = accounts.find((account) => account.strategy_id === rows[2].strategy_id && account.profile === 'STRESS')
  if (!firstBase || !secondStress) throw new Error('strategy fixture account missing')
  firstBase.open_positions = 1
  secondStress.faulted = true

  render(<StrategiesPage strategies={rows} leagueAccounts={accounts} onConfigure={vi.fn(async () => undefined)} />)

  expect(screen.getByText('PAPER 진입 중')).toBeInTheDocument()
  expect(screen.getByText('확인 필요')).toBeInTheDocument()
  expect(screen.getByText('5개 감시 · 검증 중지 5개 · 문제 1개 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getByText(/1건 자동 관리/)).toBeInTheDocument()
})

const basePosition: LeaguePosition = {
  trade_id: 'trade-base',
  candidate_id: 'candidate-base',
  account_id: `${strategies[0].strategy_id}:BASE`,
  strategy_id: strategies[0].strategy_id,
  profile: 'BASE',
  symbol: 'BTCUSDT',
  side: 'LONG',
  signal_time: 1,
  opened_ts_ms: 1,
  actual_entry: '100',
  current_mark: '101',
  initial_stop: '99',
  current_stop: '99.5',
  TP1: '102',
  TP2: '103',
  original_quantity: '1',
  remaining_quantity: '1',
  notional: '100',
  effective_leverage: '0.1',
  gross_pnl: '1',
  fees: '0.1',
  slippage: '0.1',
  net_pnl: '0.8',
  elapsed_seconds: 30,
  exit_style: 'TWO_TARGET',
  management_reason: '진입 근거 유지',
}

test('uses BASE as the default open-trade filter and can reveal STRESS', () => {
  const stressPosition = { ...basePosition, trade_id: 'trade-stress', candidate_id: 'candidate-stress', account_id: `${strategies[0].strategy_id}:STRESS`, profile: 'STRESS' as const, symbol: 'ETHUSDT' }
  render(<LeaguePositionsPage positions={[basePosition, stressPosition]} strategies={strategies} />)
  expect(screen.getByRole('button', { name: 'BASE' })).toHaveAttribute('aria-pressed', 'true')
  expect(document.querySelector('tbody')?.textContent).toContain('BTCUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('ETHUSDT')
  fireEvent.click(screen.getByRole('button', { name: 'STRESS' }))
  expect(document.querySelector('tbody')?.textContent).toContain('ETHUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('BTCUSDT')
})

test('uses current-version report costs and drawdown in stored performance statistics', () => {
  const data = dashboardFixture()
  const first = data.strategies[0]
  first.performance.BASE = {
    ...first.performance.BASE,
    fees: '12.34',
    slippage: '23.45',
    maximum_drawdown: '34.56',
    excluded_prior_version_samples: 7,
  }
  const firstAccount = data.league_accounts.find((account) => account.strategy_id === first.strategy_id && account.profile === 'BASE')
  if (!firstAccount) throw new Error('BASE fixture account missing')
  firstAccount.fees_usdt = '91.11'
  firstAccount.slippage_usdt = '92.22'
  firstAccount.maximum_drawdown_usdt = '93.33'

  render(<PerformancePage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)

  expect(screen.getByText(/요약·현재자산은 이번 Run/)).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '이번 Run 현재자산' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '현재버전 거래·승률' })).toBeInTheDocument()
  expect(screen.getByText('이번 Run BASE 완료 거래')).toBeInTheDocument()
  const storedStatistics = document.querySelector('.strategy-performance-panel')?.textContent ?? ''
  expect(storedStatistics).toContain('12.34 USDT')
  expect(storedStatistics).toContain('23.45 USDT')
  expect(storedStatistics).toContain('34.56 USDT')
  expect(storedStatistics).toContain('과거 버전 7건 제외')
  expect(storedStatistics).not.toContain('91.11 USDT')
  expect(storedStatistics).not.toContain('92.22 USDT')
  expect(storedStatistics).not.toContain('93.33 USDT')
})

test('derives strategy and account totals from the backend registry payload', () => {
  const data = dashboardFixture()
  const template = data.strategies[0]
  const extraId = 'SYNTHETIC_REGISTRY_EXTENSION_V1'
  const extraStrategy = {
    ...template,
    strategy_id: extraId,
    short_name: '확장 확인',
    display_name_ko: '동적 Registry 확장 확인',
    governance: { ...template.governance, strategy_id: extraId },
    performance: {
      BASE: { ...template.performance.BASE, strategy_id: extraId },
      STRESS: { ...template.performance.STRESS, strategy_id: extraId },
    },
  }
  const extraAccounts = (['BASE', 'STRESS'] as const).map((profile) => ({
    ...data.league_accounts.find((account) => account.profile === profile)!,
    account_id: `${extraId}:${profile}`,
    strategy_id: extraId,
    profile,
  }))
  data.strategies = [...data.strategies, extraStrategy]
  data.league_accounts = [...data.league_accounts, ...extraAccounts]

  render(<PerformancePage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)

  expect(screen.getByText(`${data.strategies.length}개 전략의 독립 가상계좌를 같은 기준으로 비교합니다.`)).toBeInTheDocument()
  expect(screen.getByText(`총 ${data.league_accounts.length}계좌`)).toBeInTheDocument()
  expect(screen.getAllByText('확장 확인')).toHaveLength(2)
})

test('shows current strategy version scope and excluded prior samples for symbol analytics', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    generated_ts_ms: 1,
    rows: [],
    ranking_rule: '표본 30건',
    analysis_scope: 'CURRENT_STRATEGY_VERSION',
    strategy_version: 'fixture-current',
    excluded_prior_version_samples: 19,
    real_orders_enabled: false,
    auth_required: false,
  }), { status: 200, headers: { 'content-type': 'application/json' } })))

  render(<StrategySymbolPage strategies={strategies} />)

  await waitFor(() => expect(screen.getByText(/\uACFC\uAC70 \uBC84\uC804 19\uAC74 \uC81C\uC678/)).toBeInTheDocument())
  expect(screen.getByText(/현재 전략 버전의 독립 공개시장 PAPER만 집계/)).toBeInTheDocument()
})
