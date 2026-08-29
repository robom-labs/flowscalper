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

test('shows fifteen compact strategy rows with ten simultaneous paper hypotheses', () => {
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} />)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(15)
  expect(document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')).toHaveLength(15)
  expect(screen.queryByText('기록만 하기')).not.toBeInTheDocument()
  expect(screen.getByText('10개 감시 · 검증 중지 5개 · 문제 0개 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getAllByText('준비 중')).toHaveLength(10)
  expect(document.querySelectorAll('.strategy-monitor.off')).toHaveLength(5)
  expect(screen.getByRole('columnheader', { name: '이번 실행 결과' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '검증 결과' })).toBeInTheDocument()
  expect(screen.queryByRole('columnheader', { name: '현재버전 승률' })).not.toBeInTheDocument()

  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[0])
  expect(screen.getByRole('dialog', { name: '전략 상세 정보' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '기본 비용 가상계좌' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '보수 비용 가상계좌' })).toBeInTheDocument()
  expect(screen.getAllByText(/현재 자산/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText(/현재 전략 버전의 공개시장 모의거래 기준/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('과거 버전 제외')).toHaveLength(2)
  expect(screen.getByRole('heading', { name: '자동 평가 상태' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '한눈에 보는 전략' })).toBeInTheDocument()
  expect(screen.getByText('10초~3분')).toBeInTheDocument()
  expect(screen.getByText('고급 기술 정보')).toBeInTheDocument()
  expect(screen.getByText('TOP_OF_BOOK_BASE13_STRESS25_V1')).not.toBeVisible()
  expect(screen.getByText('필요 데이터')).toBeInTheDocument()
  expect(screen.getByText('최소 준비')).toBeInTheDocument()
  expect(screen.getByText('무엇을 노리나요?')).toBeInTheDocument()
  expect(screen.getByText('반증 조건')).toBeInTheDocument()
  expect(screen.getByText('종료 원칙')).toBeInTheDocument()
  expect(screen.getByText('위험예산')).toBeInTheDocument()
  expect(screen.getByText('대상 범위')).toBeInTheDocument()
  expect(screen.getByText('미래정보 방지')).toBeInTheDocument()
  expect(screen.getByText('연구 근거')).toBeInTheDocument()
  expect(screen.getByText('현재 상태 코드')).not.toBeVisible()
  expect(screen.getByText('아직 검증 불충분')).toBeInTheDocument()
  expect(screen.getAllByText('고급 통계 보기')).toHaveLength(2)

  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[10])
  expect(screen.getByText('1시간~36시간')).toBeInTheDocument()
  expect(screen.getByText(/TP1 2.2R·40%/)).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[11])
  expect(screen.getByText('30분~8시간')).toBeInTheDocument()
  expect(screen.getByText(/일반 근거약화 조기청산 없음/)).toBeInTheDocument()
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
  const history = dialog.querySelector('ol')
  if (!history) throw new Error('strategy change history missing')
  expect(within(history).getByText(/rev 0/)).toBeInTheDocument()
  expect(within(history).getByText(/rev 1/)).toBeInTheDocument()
  expect(within(history).getByText(/전략 설정을 SHADOW 상태로 변경/)).toBeInTheDocument()
  expect(within(history).getByText(/SHADOW\|SHADOW\|LONG=ON\|SHORT=ON.*USER_UI/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '직전 설정으로 복원' }))

  await waitFor(() => expect(onRollback).toHaveBeenCalledWith(
    current.strategy_id,
    0,
    1,
  ))
})

test('explains the 70 percent retirement gate in beginner Korean', () => {
  const rows = strategies.map((strategy, index) => index === 1 ? {
    ...strategy,
    governance: {
      ...strategy.governance,
      recommended_lifecycle: 'RETIRED' as const,
      reason_codes: ['BASE_WIN_RATE_LT_0_70_AFTER_MINIMUM_EVIDENCE'],
    },
  } : strategy)

  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} />)
  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[1])

  expect(screen.getByText('충분한 기본 비용 표본에서 승률 70%에 못 미쳐 검증을 종료했습니다.')).toBeInTheDocument()
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
    evaluated_paths: index === 1 || index === 5 ? 24 : strategy.evaluated_paths,
    latest_status: index === 1 || index === 5 ? 'REJECTED' : strategy.latest_status,
    latest_reasons: index === 1 || index === 5 ? ['AGGRESSOR_FLOW_NOT_ALIGNED', 'QUEUE_ALIGNMENT_NOT_PERSISTENT'] : strategy.latest_reasons,
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
  expect(screen.getByText('조건 미충족')).toBeInTheDocument()
  expect(screen.getByText('9개 감시 · 검증 중지 5개 · 문제 1개 · 실제 주문 0')).toBeInTheDocument()
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

test('uses the basic-cost account as the default open-trade filter and can reveal conservative costs', () => {
  const stressPosition = { ...basePosition, trade_id: 'trade-stress', candidate_id: 'candidate-stress', account_id: `${strategies[0].strategy_id}:STRESS`, profile: 'STRESS' as const, symbol: 'ETHUSDT' }
  render(<LeaguePositionsPage positions={[basePosition, stressPosition]} strategies={strategies} />)
  expect(screen.getByRole('button', { name: '기본 비용' })).toHaveAttribute('aria-pressed', 'true')
  expect(document.querySelector('tbody')?.textContent).toContain('BTCUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('ETHUSDT')
  fireEvent.click(screen.getByRole('button', { name: '보수 비용' }))
  expect(document.querySelector('tbody')?.textContent).toContain('ETHUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('BTCUSDT')
})

test('shows the active runner trail in beginner Korean without hiding the stop', () => {
  const trailingPosition: LeaguePosition = {
    ...basePosition,
    trailing: {
      enabled: true,
      state: 'RUNNER_ACTIVE',
      policy_id: 'EDGE_ADAPTIVE_V1',
      model: 'EDGE_ADAPTIVE',
      activation_price: '101',
      activation_ts_ms: 2_000,
      current_trail: '100.8',
      runner_quantity: '0.6',
      giveback_usdt: '0.2',
      data_health: 'HEALTHY',
      adverse_active: true,
      adverse_reasons: ['OFI_ADVERSE', 'MICROPRICE_ADVERSE'],
    },
  }

  render(<LeaguePositionsPage positions={[trailingPosition]} strategies={strategies} />)

  expect(screen.getByText(/남은 수량 추적 중.*보호선 100.8.*추세 약화 지속 확인/)).toBeInTheDocument()
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
    trail_activation_count: 2,
    trail_activation_rate: '0.5',
    tp1_fill_rate: '0.5',
    runner_count: 1,
    runner_rate: '0.25',
    runner_net_contribution_usdt: '0.67',
    mfe_capture_ratio_mean: '0.4',
    average_peak_giveback_usdt: '0.12',
    median_peak_giveback_usdt: '0.1',
    p90_peak_giveback_usdt: '0.2',
    trailing_exit_count: 1,
    stop_before_trail_activation_count: 0,
    activation_after_net_negative_exit_count: 0,
    trail_trigger_slippage_usdt: '0.03',
  }
  const firstAccount = data.league_accounts.find((account) => account.strategy_id === first.strategy_id && account.profile === 'BASE')
  if (!firstAccount) throw new Error('BASE fixture account missing')
  firstAccount.fees_usdt = '91.11'
  firstAccount.slippage_usdt = '92.22'
  firstAccount.maximum_drawdown_usdt = '93.33'

  render(<PerformancePage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)

  expect(screen.getByText(/자산은 이번 실행/)).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '이번 실행' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '완료·승률' })).toBeInTheDocument()
  expect(screen.getByText('이번 실행 완료 거래')).toBeInTheDocument()
  const storedStatistics = document.querySelector('.strategy-performance-panel')?.textContent ?? ''
  expect(storedStatistics).toContain('12.34 USDT')
  expect(storedStatistics).toContain('23.45 USDT')
  expect(storedStatistics).toContain('34.56 USDT')
  expect(storedStatistics).toContain('추적 활성 2건')
  expect(storedStatistics).toContain('남은 수량 관리 1건')
  expect(storedStatistics).toContain('1차 목표 체결 50%')
  expect(storedStatistics).toContain('남은 수량 순기여 +0.67 USDT')
  expect(storedStatistics).toContain('되돌림 중앙 0.1 USDT / 상위 10% 0.2 USDT')
  expect(storedStatistics).toContain('추적 종료 비용 0.03 USDT')
  expect(storedStatistics).toContain('과거 버전 7건 제외')
  expect(storedStatistics).not.toContain('91.11 USDT')
  expect(storedStatistics).not.toContain('92.22 USDT')
  expect(storedStatistics).not.toContain('93.33 USDT')
})

test('hides strategy statistics while the versioned history cache is loading', () => {
  const data = dashboardFixture()
  data.system.dashboard_trade_cache_ready = false

  const { unmount } = render(<PerformancePage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)
  expect(screen.getByRole('status')).toHaveTextContent('준비가 끝나기 전에는 승률·기대값·순위를 표시하지 않습니다.')
  expect(document.querySelector('.strategy-performance-panel')?.textContent).toContain('불러오는 중')
  unmount()

  render(<StrategiesPage strategies={data.strategies} leagueAccounts={data.league_accounts} analyticsReady={false} onConfigure={vi.fn(async () => undefined)} />)
  expect(screen.getByRole('status')).toHaveTextContent('준비 전 숫자는 순위나 승률로 사용하지 않습니다.')
  expect(document.querySelector('.strategy-compact-table')?.textContent).toContain('불러오는 중')
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

  expect(screen.getByText(`${data.strategies.length}개 전략을 같은 공개시장 데이터와 비용 기준으로 비교합니다.`)).toBeInTheDocument()
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

  await waitFor(() => expect(screen.getByText(/과거 버전 19건 보관/)).toBeInTheDocument())
  expect(screen.getByText(/현재 전략 버전의 독립 공개시장 모의거래만/)).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '어떤 전략이 어떤 종목에 맞았나요?' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '기본 비용' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '보수 비용' })).toBeInTheDocument()
})
