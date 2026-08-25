// 10전략·20독립계좌가 쉬운 전략 설정과 진행 거래에서 분리 표시되는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LeaguePositionsPage } from '../src/pages/LeaguePositionsPage'
import { PerformancePage } from '../src/pages/PerformancePage'
import { StrategiesPage } from '../src/pages/StrategiesPage'
import { StrategySymbolPage } from '../src/pages/StrategySymbolPage'
import type { LeaguePosition } from '../src/types'
import { dashboardFixture, leagueAccounts, strategies } from './fixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('shows ten compact strategy rows, easy modes and BASE/STRESS account detail', () => {
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} />)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(10)
  expect(document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')).toHaveLength(10)
  expect(screen.queryByText('기록만 하기')).not.toBeInTheDocument()
  expect(screen.getByText('10개 감시 · 검증 중지 0개 · 문제 0개 · 실제 주문 0')).toBeInTheDocument()
  expect(screen.getAllByText('준비 중')).toHaveLength(10)
  expect([...document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')].every((button) => button.textContent?.includes('모의 중'))).toBe(true)

  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[0])
  expect(screen.getByRole('dialog', { name: '전략 상세 정보' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'BASE 가상계좌' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'STRESS 가상계좌' })).toBeInTheDocument()
  expect(screen.getAllByText(/이번 Run 현재자산/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText(/현재 전략 버전의 공개시장 PAPER 기준/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('과거 버전 제외')).toHaveLength(2)
})

test('distinguishes healthy condition waiting, open PAPER management and faults', () => {
  const rows = strategies.map((strategy, index) => ({
    ...strategy,
    evaluated_paths: index === 0 ? 24 : strategy.evaluated_paths,
    latest_status: index === 0 ? 'REJECTED' : strategy.latest_status,
    latest_reasons: index === 0 ? ['AGGRESSOR_FLOW_NOT_ALIGNED', 'QUEUE_ALIGNMENT_NOT_PERSISTENT'] : strategy.latest_reasons,
  }))
  const accounts = leagueAccounts.map((account) => ({ ...account }))
  const firstBase = accounts.find((account) => account.strategy_id === rows[0].strategy_id && account.profile === 'BASE')
  const secondStress = accounts.find((account) => account.strategy_id === rows[1].strategy_id && account.profile === 'STRESS')
  if (!firstBase || !secondStress) throw new Error('strategy fixture account missing')
  firstBase.open_positions = 1
  secondStress.faulted = true

  render(<StrategiesPage strategies={rows} leagueAccounts={accounts} onConfigure={vi.fn(async () => undefined)} />)

  expect(screen.getByText('PAPER 진입 중')).toBeInTheDocument()
  expect(screen.getByText('확인 필요')).toBeInTheDocument()
  expect(screen.getByText('9개 감시 · 검증 중지 0개 · 문제 1개 · 실제 주문 0')).toBeInTheDocument()
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
